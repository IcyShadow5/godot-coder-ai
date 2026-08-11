from __future__ import annotations
"""The TinyGPT architecture: RMSNorm, SwiGLU, RoPE, and a compact transformer."""

import math
from dataclasses import dataclass
from typing import Iterator, TypeAlias

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.checkpoint import checkpoint

from .config import ModelConfig

KVCache: TypeAlias = list[tuple[torch.Tensor, torch.Tensor]]


class RMSNorm(nn.Module):
    """RMSNorm: normalise over the last dimension only, skip the mean for speed."""

    def __init__(self, dimension: int, epsilon: float = 1e-6) -> None:
        super().__init__()
        self.weight = nn.Parameter(torch.ones(dimension))
        self.epsilon = epsilon

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Cast to float32 for numerical stability, then back to the input dtype.
        normalized = x.float() * torch.rsqrt(x.float().pow(2).mean(dim=-1, keepdim=True) + self.epsilon)
        return (normalized * self.weight.float()).to(dtype=x.dtype)


def build_rope_cache(head_dim: int, max_seq_len: int, base: float) -> tuple[torch.Tensor, torch.Tensor]:
    inv_freq = 1.0 / (base ** (torch.arange(0, head_dim, 2, dtype=torch.float32) / head_dim))
    positions = torch.arange(max_seq_len, dtype=torch.float32)
    frequencies = torch.outer(positions, inv_freq)
    return frequencies.cos(), frequencies.sin()


def apply_rope(x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
    # Rotary Position Embedding: rotate pairs of dimensions by position-dependent angles.
    even, odd = x[..., 0::2], x[..., 1::2]
    cos = cos.unsqueeze(0).unsqueeze(0).to(dtype=x.dtype)
    sin = sin.unsqueeze(0).unsqueeze(0).to(dtype=x.dtype)
    return torch.stack((even * cos - odd * sin, even * sin + odd * cos), dim=-1).flatten(-2)


class CausalSelfAttention(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.n_heads = config.n_heads
        self.head_dim = config.d_model // config.n_heads
        self.dropout = config.dropout
        self.qkv = nn.Linear(config.d_model, 3 * config.d_model, bias=False)
        self.out_proj = nn.Linear(config.d_model, config.d_model, bias=False)

    def forward(
        self,
        x: torch.Tensor,
        cos: torch.Tensor,
        sin: torch.Tensor,
        past: tuple[torch.Tensor, torch.Tensor] | None = None,
        use_cache: bool = False,
    ) -> tuple[torch.Tensor, tuple[torch.Tensor, torch.Tensor] | None]:
        batch, sequence, channels = x.shape
        qkv = self.qkv(x).view(batch, sequence, 3, self.n_heads, self.head_dim)
        q, k, v = qkv.permute(2, 0, 3, 1, 4).unbind(0)
        q, k = apply_rope(q, cos, sin), apply_rope(k, cos, sin)
        if past is not None:
            past_k, past_v = past
            k = torch.cat((past_k, k), dim=2)
            v = torch.cat((past_v, v), dim=2)
        # When past is not None and only one new token arrives, SDPA sees
        # sequence=1 and ignores the mask — no special casing needed. For chunked
        # prefill (past + sequence > 1) the causal mask must stay on so new tokens
        # cannot attend to each other.
        causal = sequence > 1
        attended = F.scaled_dot_product_attention(
            q, k, v,
            dropout_p=self.dropout if self.training else 0.0,
            is_causal=causal,
        )
        attended = attended.transpose(1, 2).contiguous().view(batch, sequence, channels)
        cache = (k.detach(), v.detach()) if use_cache else None
        return self.out_proj(attended), cache


class SwiGLU(nn.Module):
    """SwiGLU: half the inner projection acts as a learnable gate, the other half as values."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        # Project to 2*d_ff so we can split into gate and value halves.
        self.in_proj = nn.Linear(config.d_model, 2 * config.d_ff, bias=False)
        self.out_proj = nn.Linear(config.d_ff, config.d_model, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        gate, value = self.in_proj(x).chunk(2, dim=-1)
        return self.out_proj(F.silu(gate) * value)


class TransformerBlock(nn.Module):
    """Pre-norm transformer block: attention then MLP, each with a residual skip."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        # Pre-norm: normalise before attention/MLP, not after (better training stability).
        self.attn_norm = RMSNorm(config.d_model)
        self.attn = CausalSelfAttention(config)
        self.mlp_norm = RMSNorm(config.d_model)
        self.mlp = SwiGLU(config)

    def forward(self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
        # Self-attention with residual skip.
        attended, _ = self.attn(self.attn_norm(x), cos, sin)
        x = x + attended
        # SwiGLU feed-forward with residual skip.
        return x + self.mlp(self.mlp_norm(x))

    def forward_cached(
        self,
        x: torch.Tensor,
        cos: torch.Tensor,
        sin: torch.Tensor,
        past: tuple[torch.Tensor, torch.Tensor] | None,
    ) -> tuple[torch.Tensor, tuple[torch.Tensor, torch.Tensor]]:
        attended, cache = self.attn(self.attn_norm(x), cos, sin, past=past, use_cache=True)
        if cache is None:
            raise RuntimeError("attention cache was not produced")
        x = x + attended
        return x + self.mlp(self.mlp_norm(x)), cache


@dataclass
class ModelOutput:
    logits: torch.Tensor
    loss: torch.Tensor | None = None


class TinyGPT(nn.Module):
    """Decoder-only Transformer using RoPE, RMSNorm, SwiGLU, causal SDPA and optional KV caching."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        config.validate()
        self.config = config
        self.token_embedding = nn.Embedding(config.vocab_size, config.d_model)
        self.blocks = nn.ModuleList([TransformerBlock(config) for _ in range(config.n_layers)])
        self.final_norm = RMSNorm(config.d_model)
        self.lm_head = nn.Linear(config.d_model, config.vocab_size, bias=False)
        if config.tie_embeddings:
            self.lm_head.weight = self.token_embedding.weight
        head_dim = config.d_model // config.n_heads
        rope_cos, rope_sin = build_rope_cache(head_dim, config.max_seq_len, config.rope_base)
        self.register_buffer("rope_cos", rope_cos, persistent=False)
        self.register_buffer("rope_sin", rope_sin, persistent=False)
        self.apply(self._initialize_weights)
        residual_std = 0.02 / math.sqrt(2 * config.n_layers)
        for block in self.blocks:
            nn.init.normal_(block.attn.out_proj.weight, mean=0.0, std=residual_std)
            nn.init.normal_(block.mlp.out_proj.weight, mean=0.0, std=residual_std)

    @staticmethod
    def _initialize_weights(module: nn.Module) -> None:
        if isinstance(module, nn.Linear):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def _embed(self, input_ids: torch.Tensor) -> torch.Tensor:
        x = self.token_embedding(input_ids)
        if torch.is_autocast_enabled(x.device.type):
            x = x.to(dtype=torch.get_autocast_dtype(x.device.type))
        return x

    def forward(self, input_ids: torch.Tensor, targets: torch.Tensor | None = None) -> ModelOutput:
        _, sequence = input_ids.shape
        if sequence > self.config.max_seq_len:
            raise ValueError(f"sequence length {sequence} exceeds configured maximum {self.config.max_seq_len}")
        x = self._embed(input_ids)
        cos, sin = self.rope_cos[:sequence], self.rope_sin[:sequence]
        for block in self.blocks:
            if self.config.gradient_checkpointing and self.training:
                x = checkpoint(block, x, cos, sin, use_reentrant=False, preserve_rng_state=True)
            else:
                x = block(x, cos, sin)
        logits = self.lm_head(self.final_norm(x))
        loss = F.cross_entropy(logits.reshape(-1, logits.size(-1)), targets.reshape(-1)) if targets is not None else None
        return ModelOutput(logits=logits, loss=loss)

    def forward_cached(self, input_ids: torch.Tensor, past_key_values: KVCache | None = None) -> tuple[torch.Tensor, KVCache]:
        past_length = 0 if not past_key_values else int(past_key_values[0][0].shape[2])
        sequence = input_ids.shape[1]
        if past_length + sequence > self.config.max_seq_len:
            raise ValueError("cached sequence exceeds configured maximum context")
        x = self._embed(input_ids)
        cos = self.rope_cos[past_length:past_length + sequence]
        sin = self.rope_sin[past_length:past_length + sequence]
        next_cache: KVCache = []
        for index, block in enumerate(self.blocks):
            past = past_key_values[index] if past_key_values else None
            x, cache = block.forward_cached(x, cos, sin, past)
            next_cache.append(cache)
        return self.lm_head(self.final_norm(x)), next_cache

    @staticmethod
    def _apply_repetition_penalty(
        logits: torch.Tensor,
        past_ids: torch.Tensor | None,
        penalty: float,
    ) -> torch.Tensor:
        """HF-style repetition penalty on the tokens generated so far.

        Logits of tokens that already appeared in the current completion are
        divided by the penalty (multiplied when negative), nudging the model
        toward novel tokens. A penalty of 1.0 is a no-op.
        """
        if penalty <= 0 or penalty == 1.0 or past_ids is None or past_ids.numel() == 0:
            return logits
        gathered = logits.gather(1, past_ids)
        adjusted = torch.where(gathered > 0, gathered / penalty, gathered * penalty)
        return logits.scatter(1, past_ids, adjusted)

    @staticmethod
    def _apply_top_p(logits: torch.Tensor, top_p: float) -> torch.Tensor:
        """Nucleus masking: keep the smallest token set whose cumulative
        probability exceeds top_p and mask the rest to -inf."""
        sorted_logits, sorted_indices = torch.sort(logits, dim=-1, descending=True)
        probs = F.softmax(sorted_logits, dim=-1)
        cumulative = torch.cumsum(probs, dim=-1)
        keep = cumulative - probs <= top_p
        sorted_logits = sorted_logits.masked_fill(~keep, float("-inf"))
        return sorted_logits.scatter(-1, sorted_indices, sorted_logits)

    @staticmethod
    def _sample(
        logits: torch.Tensor,
        temperature: float,
        top_k: int | None,
        *,
        top_p: float | None = None,
        repetition_penalty: float = 1.0,
        past_ids: torch.Tensor | None = None,
    ) -> torch.Tensor:
        if repetition_penalty != 1.0:
            logits = TinyGPT._apply_repetition_penalty(logits, past_ids, repetition_penalty)
        if temperature <= 0:
            return torch.argmax(logits, dim=-1, keepdim=True)
        logits = logits / temperature
        if top_k is not None and 0 < top_k < logits.size(-1):
            threshold = torch.topk(logits, top_k).values[:, -1].unsqueeze(-1)
            logits = logits.masked_fill(logits < threshold, float("-inf"))
        if top_p is not None and 0.0 < top_p < 1.0:
            logits = TinyGPT._apply_top_p(logits, top_p)
        return torch.multinomial(F.softmax(logits, dim=-1), num_samples=1)

    def generate_stream(
        self,
        input_ids: torch.Tensor,
        *,
        max_new_tokens: int,
        temperature: float = 0.8,
        top_k: int | None = 40,
        top_p: float | None = None,
        repetition_penalty: float = 1.0,
        eos_id: int | None = None,
        use_kv_cache: bool = True,
    ) -> Iterator[torch.Tensor]:
        """Yield each freshly sampled token id ([1, 1] tensor) as it appears.

        The KV cache stays alive across yields, so after the first step every
        forward pass only sees the most recent token. `generate` is exactly
        the concatenation of this stream - the two must never drift apart.
        """
        if max_new_tokens < 0:
            raise ValueError("max_new_tokens cannot be negative")
        if not math.isfinite(temperature) or temperature < 0:
            raise ValueError("temperature must be finite and non-negative")
        if top_k is not None and top_k < 0:
            raise ValueError("top_k cannot be negative")
        if top_p is not None and not 0.0 < top_p <= 1.0:
            raise ValueError("top_p must be in (0, 1]")
        if repetition_penalty <= 0:
            raise ValueError("repetition_penalty must be positive")
        # Decoding runs with dropout off, but the mode flip must not leak
        # into the caller: the training loop may call generate_stream on a
        # train-mode model, and a permanent eval() would silently disable
        # dropout for every later step. Restore the previous mode on exit -
        # also when the consumer closes the stream early (finally still runs).
        was_training = self.training
        self.eval()
        try:
            # Only the freshly generated ids are penalized, never the prompt.
            generated: list[torch.Tensor] = []
            with torch.no_grad():
                if not use_kv_cache or input_ids.shape[1] + max_new_tokens > self.config.max_seq_len:
                    for _ in range(max_new_tokens):
                        context = input_ids[:, -self.config.max_seq_len:]
                        next_id = self._sample(
                            self(context).logits[:, -1, :],
                            temperature,
                            top_k,
                            top_p=top_p,
                            repetition_penalty=repetition_penalty,
                            past_ids=torch.cat(generated, dim=1) if generated else None,
                        )
                        generated.append(next_id)
                        input_ids = torch.cat((input_ids, next_id), dim=1)
                        yield next_id
                        if eos_id is not None and torch.all(next_id == eos_id):
                            return
                    return
                logits, cache = self.forward_cached(input_ids)
                for _ in range(max_new_tokens):
                    next_id = self._sample(
                        logits[:, -1, :],
                        temperature,
                        top_k,
                        top_p=top_p,
                        repetition_penalty=repetition_penalty,
                        past_ids=torch.cat(generated, dim=1) if generated else None,
                    )
                    generated.append(next_id)
                    input_ids = torch.cat((input_ids, next_id), dim=1)
                    yield next_id
                    if eos_id is not None and torch.all(next_id == eos_id):
                        return
                    logits, cache = self.forward_cached(next_id, cache)
        finally:
            if was_training:
                self.train()

    @torch.no_grad()
    def generate(
        self,
        input_ids: torch.Tensor,
        *,
        max_new_tokens: int,
        temperature: float = 0.8,
        top_k: int | None = 40,
        top_p: float | None = None,
        repetition_penalty: float = 1.0,
        eos_id: int | None = None,
        use_kv_cache: bool = True,
    ) -> torch.Tensor:
        """Generate up to max_new_tokens tokens; returns prompt plus new tokens."""
        generated = list(
            self.generate_stream(
                input_ids,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                top_k=top_k,
                top_p=top_p,
                repetition_penalty=repetition_penalty,
                eos_id=eos_id,
                use_kv_cache=use_kv_cache,
            )
        )
        if not generated:
            return input_ids
        return torch.cat([input_ids, *generated], dim=1)

    def parameter_count(self, *, trainable_only: bool = True) -> int:
        parameters = self.parameters()
        if trainable_only:
            parameters = (parameter for parameter in parameters if parameter.requires_grad)
        return sum(parameter.numel() for parameter in parameters)
