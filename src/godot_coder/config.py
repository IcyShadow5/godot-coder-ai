from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ModelConfig:
    vocab_size: int = 269
    max_seq_len: int = 256
    n_layers: int = 4
    d_model: int = 192
    n_heads: int = 6
    d_ff: int = 512
    dropout: float = 0.0
    rope_base: float = 10_000.0
    tie_embeddings: bool = True
    gradient_checkpointing: bool = False

    def validate(self) -> None:
        if self.vocab_size <= 0:
            raise ValueError("vocab_size must be positive")
        if self.vocab_size > 2**32 - 1:
            raise ValueError("vocab_size exceeds the supported token id range")
        if self.max_seq_len < 8:
            raise ValueError("max_seq_len must be at least 8")
        if self.d_model <= 0 or self.n_heads <= 0:
            raise ValueError("d_model and n_heads must be positive")
        if self.d_model % self.n_heads != 0:
            raise ValueError("d_model must be divisible by n_heads")
        head_dim = self.d_model // self.n_heads
        if head_dim % 2 != 0:
            raise ValueError("attention head dimension must be even for RoPE")
        if self.d_ff <= 0 or self.n_layers <= 0:
            raise ValueError("d_ff and n_layers must be positive")
        if self.rope_base <= 1.0:
            raise ValueError("rope_base must be greater than 1")
        if not 0.0 <= self.dropout < 1.0:
            raise ValueError("dropout must be in [0, 1)")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrainConfig:
    tokenizer_path: str = "artifacts/tokenizer.json"
    data_dir: str = "data/processed"
    output_dir: str = "checkpoints/tiny"
    device: str = "auto"
    dtype: str = "float16"
    seed: int = 1337
    batch_size: int = 8
    gradient_accumulation_steps: int = 4
    max_steps: int | None = 1500
    max_tokens: int | None = None
    target_dataset_passes: float | None = None
    learning_rate: float = 4e-4
    min_learning_rate: float = 4e-5
    warmup_steps: int = 100
    weight_decay: float = 0.1
    beta1: float = 0.9
    beta2: float = 0.95
    gradient_clip: float = 1.0
    log_interval: int = 10
    eval_interval: int = 100
    validation_interval_tokens: int | None = None
    eval_batches: int = 20
    evaluation_mode: str = "fixed"
    evaluation_seed: int = 7331
    evaluation_stride: int | None = None
    save_interval: int = 100
    save_best_only: bool = False
    keep_last_checkpoints: int = 3
    early_stopping_enabled: bool = False
    early_stopping_patience: int = 5
    early_stopping_min_delta: float = 0.01
    max_dataset_passes_warning: float = 8.0
    max_dataset_passes_block: float = 50.0
    allow_excessive_dataset_passes: bool = False
    compile_model: bool = False
    compile_mode: str = "default"
    prefetch_batches: int = 0

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "TrainConfig":
        data = dict(raw)
        early = data.pop("early_stopping", None) or {}
        if early:
            data.setdefault("early_stopping_enabled", bool(early.get("enabled", False)))
            data.setdefault("early_stopping_patience", int(early.get("patience", 5)))
            data.setdefault("early_stopping_min_delta", float(early.get("min_delta", 0.01)))
        compile_raw = data.pop("compile", None) or {}
        if compile_raw:
            data.setdefault("compile_model", bool(compile_raw.get("enabled", False)))
            data.setdefault("compile_mode", str(compile_raw.get("mode", "default")))
        return cls(**data)

    def validate(self) -> None:
        positive_ints = {
            "batch_size": self.batch_size,
            "gradient_accumulation_steps": self.gradient_accumulation_steps,
            "log_interval": self.log_interval,
            "eval_batches": self.eval_batches,
            "save_interval": self.save_interval,
            "early_stopping_patience": self.early_stopping_patience,
        }
        if self.max_steps is not None:
            positive_ints["max_steps"] = self.max_steps
        for name, value in positive_ints.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.max_steps is None and self.max_tokens is None and self.target_dataset_passes is None:
            raise ValueError("at least one of max_steps, max_tokens, or target_dataset_passes is required")
        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if self.target_dataset_passes is not None and self.target_dataset_passes <= 0:
            raise ValueError("target_dataset_passes must be positive")
        if self.warmup_steps < 0:
            raise ValueError("warmup_steps must be non-negative")
        if self.max_steps is not None and self.warmup_steps >= self.max_steps:
            raise ValueError("warmup_steps must be smaller than max_steps")
        if self.keep_last_checkpoints < 0 or self.prefetch_batches < 0:
            raise ValueError("checkpoint retention and prefetch values cannot be negative")
        if self.learning_rate <= 0 or self.min_learning_rate < 0:
            raise ValueError("learning rates must be non-negative and peak LR positive")
        if self.min_learning_rate > self.learning_rate:
            raise ValueError("min_learning_rate cannot exceed learning_rate")
        if self.weight_decay < 0:
            raise ValueError("weight_decay cannot be negative")
        if not 0.0 <= self.beta1 < 1.0 or not 0.0 <= self.beta2 < 1.0:
            raise ValueError("optimizer beta values must be in [0, 1)")
        if self.gradient_clip <= 0:
            raise ValueError("gradient_clip must be positive")
        if self.dtype not in {"float32", "float16", "bfloat16"}:
            raise ValueError("dtype must be float32, float16, or bfloat16")
        if self.evaluation_mode not in {"fixed", "random", "sliding"}:
            raise ValueError("evaluation_mode must be fixed, random, or sliding")
        if self.validation_interval_tokens is not None and self.validation_interval_tokens <= 0:
            raise ValueError("validation_interval_tokens must be positive")
        if self.eval_interval <= 0:
            raise ValueError("eval_interval must be positive")
        if self.early_stopping_min_delta < 0:
            raise ValueError("early_stopping_min_delta cannot be negative")
        if self.max_dataset_passes_warning <= 0 or self.max_dataset_passes_block <= 0:
            raise ValueError("dataset-pass thresholds must be positive")
        if self.max_dataset_passes_block < self.max_dataset_passes_warning:
            raise ValueError("max_dataset_passes_block cannot be below warning threshold")
        if self.compile_mode not in {"default", "reduce-overhead", "max-autotune"}:
            raise ValueError("unsupported torch.compile mode")

    def resolve_max_steps(self, *, train_tokens: int, tokens_per_optimizer_step: int, start_step: int = 0) -> int:
        if train_tokens <= 0 or tokens_per_optimizer_step <= 0:
            raise ValueError("train_tokens and tokens_per_optimizer_step must be positive")
        candidates: list[int] = []
        if self.max_steps is not None:
            candidates.append(int(self.max_steps))
        if self.max_tokens is not None:
            candidates.append(start_step + math.ceil(self.max_tokens / tokens_per_optimizer_step))
        if self.target_dataset_passes is not None:
            target_tokens = math.ceil(train_tokens * self.target_dataset_passes)
            candidates.append(start_step + math.ceil(target_tokens / tokens_per_optimizer_step))
        resolved = min(candidates)
        if resolved <= start_step:
            raise ValueError("resolved training plan contains no new optimizer steps")
        return resolved

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["early_stopping"] = {
            "enabled": self.early_stopping_enabled,
            "patience": self.early_stopping_patience,
            "min_delta": self.early_stopping_min_delta,
        }
        payload["compile"] = {"enabled": self.compile_model, "mode": self.compile_mode}
        return payload


def load_config(path: str | Path) -> tuple[ModelConfig, TrainConfig]:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict) or "model" not in raw or "train" not in raw:
        raise ValueError("config must contain 'model' and 'train' mappings")
    model = ModelConfig(**raw["model"])
    train = TrainConfig.from_mapping(raw["train"])
    model.validate()
    train.validate()
    return model, train
