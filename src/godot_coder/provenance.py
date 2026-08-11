"""Context provenance: make every model call's context measurable.

Each inference path (Studio chat, CLI, benchmark) builds its prompt from
named parts. This module counts tokens per part, checks the total against
the model window, and decides what survives when the prompt no longer
fits - so the Studio can show where the context came from and what had to
give way, instead of the model silently dropping the prompt head in its
sliding-window fallback.

The parts are joined without separators: the format lives in the part
texts themselves (for chat that reproduces the old frontend string
byte-for-byte). The byte tokenizer maps each part independently, so the
per-part tokens sum to the joined count except at special-token
boundaries (a part literally ending in "<file_sep>" etc.); the report's
prompt_tokens is always measured on the joined string, so it is the
exact count the model sees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .tokenizer import TokenizerLike

#: The header the model was trained on; wrapping requests in the same shape
#: keeps small checkpoints in distribution (the old frontend did this).
SOURCE_HEADER = "# file: chat/generated\n# task: "

#: Truncation policies. Head-preserving is the default: flexible parts lose
#: their tail first (reverse order), so code heads keep their structure.
HEAD_PRESERVING = "head_preserving"
STRICT = "strict"  # refuse to truncate, fail with a clear message instead


@dataclass(frozen=True)
class PromptPart:
    """One named chunk of a prompt with its provenance.

    ``keep`` marks machine-generated format parts that must never be
    trimmed; only flexible content parts may lose their tail.
    """

    source: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict)
    keep: bool = False


@dataclass(frozen=True)
class PartReport:
    """Per-source contribution after any trimming."""

    source: str
    meta: dict[str, Any]
    chars: int
    tokens: int
    dropped_tokens: int
    pct: float  # share of the final prompt, 0-100

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "meta": self.meta,
            "chars": self.chars,
            "tokens": self.tokens,
            "dropped_tokens": self.dropped_tokens,
            "pct": round(self.pct, 1),
        }


@dataclass(frozen=True)
class ContextReport:
    """Everything a caller needs to explain one model call's context."""

    parts: tuple[PartReport, ...]
    prompt_tokens: int  # exact tokens fed to the model (includes BOS)
    max_seq_len: int
    max_new_tokens: int
    prompt_budget: int  # tokens available for the prompt (window minus BOS)
    overflow_tokens: int  # prompt tokens beyond the budget before trimming
    dropped_tokens: int  # what the policy actually dropped
    truncated: bool
    kv_cache_possible: bool  # prompt + new tokens fit, streaming stays fast
    policy: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "parts": [part.to_dict() for part in self.parts],
            "prompt_tokens": self.prompt_tokens,
            "max_seq_len": self.max_seq_len,
            "max_new_tokens": self.max_new_tokens,
            "prompt_budget": self.prompt_budget,
            "overflow_tokens": self.overflow_tokens,
            "dropped_tokens": self.dropped_tokens,
            "truncated": self.truncated,
            "kv_cache_possible": self.kv_cache_possible,
            "policy": self.policy,
        }


def _count_tokens(tokenizer: TokenizerLike, text: str) -> int:
    return len(tokenizer.encode(text, add_bos=False))


def _trim_tail(text: str, tokenizer: TokenizerLike, max_tokens: int) -> str:
    """Cut ``text`` from the end until it fits ``max_tokens``.

    Binary search over character length: token counts are monotonic in the
    prefix length, so this converges in O(log n) encodes. Slicing happens
    on character boundaries, so the result is always valid UTF-8.
    """
    if max_tokens <= 0:
        return ""
    if _count_tokens(tokenizer, text) <= max_tokens:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _count_tokens(tokenizer, text[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


def chat_parts(prompt: str, task_format: bool) -> list[PromptPart]:
    """Build the chat prompt parts.

    With task format enabled the request is wrapped exactly like the old
    frontend did it; the header is fixed so truncation can only ever eat
    into the request text. A prompt that already carries the header (an
    older client that wrapped client-side) is left untouched.
    """
    if not task_format or prompt.startswith(SOURCE_HEADER):
        return [PromptPart("user_prompt", prompt)]
    return [
        PromptPart("task_header", SOURCE_HEADER, keep=True),
        PromptPart("user_prompt", prompt),
        PromptPart("task_tail", "\n", keep=True),
    ]


def cli_parts(prompt: str, prompt_file: str | None) -> list[PromptPart]:
    """Build the CLI prompt parts, tagged with where the text came from."""
    if prompt_file:
        return [PromptPart("prompt_file", prompt, meta={"file": prompt_file})]
    return [PromptPart("cli_prompt", prompt)]


def compose_prompt(
    parts: list[PromptPart],
    tokenizer: TokenizerLike,
    *,
    max_seq_len: int,
    max_new_tokens: int,
    policy: str = HEAD_PRESERVING,
    add_bos: bool = True,
) -> tuple[str, ContextReport]:
    """Join the parts and report how the context fits the window.

    Returns the final prompt string plus a ContextReport. With
    HEAD_PRESERVING the flexible parts lose their tails (in reverse order)
    until the prompt fits the window minus one BOS token; STRICT raises a
    ValueError instead of trimming. The model's sliding-window fallback
    stays as a last-resort safety net, surfaced as ``kv_cache_possible``.
    """
    if max_seq_len < 2:
        raise ValueError("max_seq_len must be at least 2")
    if max_new_tokens < 0:
        raise ValueError("max_new_tokens cannot be negative")
    if policy not in (HEAD_PRESERVING, STRICT):
        raise ValueError(f"unknown policy: {policy!r}")

    bos_cost = 1 if add_bos else 0
    prompt_budget = max_seq_len - bos_cost

    fixed_tokens = sum(_count_tokens(tokenizer, part.text) for part in parts if part.keep)
    if fixed_tokens >= prompt_budget:
        raise ValueError(
            f"context window too small: fixed parts need {fixed_tokens} tokens, "
            f"budget is {prompt_budget}"
        )

    original: dict[int, int] = {
        index: _count_tokens(tokenizer, part.text) for index, part in enumerate(parts)
    }
    full_tokens = sum(original.values())
    overflow = max(0, full_tokens - prompt_budget)

    trimmed = list(parts)
    dropped = 0
    if overflow > 0:
        if policy == STRICT:
            raise ValueError(
                f"context would exceed the window: {full_tokens} prompt tokens "
                f"vs {prompt_budget} available (max_seq_len={max_seq_len}); "
                "raise max_seq_len or shorten the prompt"
            )
        remaining = overflow
        for index in range(len(trimmed) - 1, -1, -1):
            part = trimmed[index]
            if part.keep or remaining <= 0:
                continue
            current = _count_tokens(tokenizer, part.text)
            target = max(0, current - remaining)
            cut = _trim_tail(part.text, tokenizer, target)
            lost = current - _count_tokens(tokenizer, cut)
            dropped += lost
            remaining -= lost
            trimmed[index] = PromptPart(part.source, cut, part.meta, part.keep)

    final = tuple(trimmed)
    prompt = "".join(part.text for part in final)
    prompt_tokens = _count_tokens(tokenizer, prompt) + bos_cost
    kept_share = max(1, prompt_tokens - bos_cost)

    part_reports = tuple(
        PartReport(
            source=part.source,
            meta=dict(part.meta),
            chars=len(part.text),
            tokens=_count_tokens(tokenizer, part.text),
            dropped_tokens=max(0, original[index] - _count_tokens(tokenizer, part.text)),
            pct=_count_tokens(tokenizer, part.text) / kept_share * 100.0,
        )
        for index, part in enumerate(final)
    )

    return prompt, ContextReport(
        parts=part_reports,
        prompt_tokens=prompt_tokens,
        max_seq_len=max_seq_len,
        max_new_tokens=max_new_tokens,
        prompt_budget=prompt_budget,
        overflow_tokens=overflow,
        dropped_tokens=dropped,
        truncated=dropped > 0,
        kv_cache_possible=prompt_tokens + max_new_tokens <= max_seq_len,
        policy=policy,
    )
