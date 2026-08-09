from __future__ import annotations
"""Checkpoint save/load with atomic writes, hard-link aliases, and RNG state capture."""

import os
import random
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import torch

CHECKPOINT_FORMAT_VERSION = 1


def capture_rng_state() -> dict[str, Any]:
    state: dict[str, Any] = {
        "python": random.getstate(),
        "numpy": np.random.get_state(),
        "torch": torch.get_rng_state(),
    }
    if torch.cuda.is_available():
        state["cuda"] = torch.cuda.get_rng_state_all()
    return state


def restore_rng_state(state: dict[str, Any]) -> None:
    random.setstate(state["python"])
    np.random.set_state(state["numpy"])
    torch.set_rng_state(state["torch"])
    if torch.cuda.is_available() and "cuda" in state:
        torch.cuda.set_rng_state_all(state["cuda"])


def _replace_alias(target: Path, alias: Path) -> str:
    """Atomically point an alias at an immutable checkpoint, preferring a hard link.

    Hard links avoid tripling multi-gigabyte checkpoint storage for step/latest/best.
    Filesystems that do not support links fall back to a normal copy.
    """
    temporary = alias.with_name(f".{alias.name}.tmp")
    temporary.unlink(missing_ok=True)
    strategy = "hardlink"
    try:
        os.link(target, temporary)
    except OSError:
        strategy = "copy"
        shutil.copy2(target, temporary)
    os.replace(temporary, alias)
    return strategy


def prune_numbered_checkpoints(output_dir: str | Path, keep_last: int) -> list[Path]:
    """Delete old immutable step files while preserving latest/best aliases."""
    if keep_last <= 0:
        return []
    directory = Path(output_dir)
    numbered = sorted(directory.glob("step_*.pt"), key=lambda path: path.name)
    stale = numbered[:-keep_last]
    removed: list[Path] = []
    for path in stale:
        try:
            path.unlink()
            removed.append(path)
        except FileNotFoundError:
            continue
    return removed


def save_checkpoint(
    output_dir: str | Path,
    *,
    step: int,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scaler: torch.amp.GradScaler,
    model_config: dict[str, Any],
    train_config: dict[str, Any],
    tokenizer_fingerprint: str,
    best_val_loss: float,
    best_step: int | None = None,
    data_rng_state: dict[str, Any] | None = None,
    is_best: bool = False,
    keep_last: int = 0,
) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"step_{step:08d}.pt"
    temporary = directory / f".{target.name}.tmp"
    payload = {
        "format": "godot-coder-checkpoint",
        "format_version": CHECKPOINT_FORMAT_VERSION,
        "step": step,
        "model_state": model.state_dict(),
        "optimizer_state": optimizer.state_dict(),
        "scaler_state": scaler.state_dict(),
        "model_config": model_config,
        "train_config": train_config,
        "tokenizer_fingerprint": tokenizer_fingerprint,
        "best_val_loss": best_val_loss,
        "best_step": best_step,
        "rng_state": capture_rng_state(),
        "data_rng_state": data_rng_state,
    }
    torch.save(payload, temporary)
    os.replace(temporary, target)
    _replace_alias(target, directory / "latest.pt")
    if is_best:
        _replace_alias(target, directory / "best.pt")
    prune_numbered_checkpoints(directory, keep_last)
    return target


def load_checkpoint(path: str | Path, map_location: str | torch.device = "cpu") -> dict[str, Any]:
    checkpoint_path = Path(path)
    if not checkpoint_path.exists():
        raise FileNotFoundError(checkpoint_path)
    try:
        payload = torch.load(checkpoint_path, map_location=map_location, weights_only=False)
    except TypeError:  # Compatibility with older PyTorch versions.
        payload = torch.load(checkpoint_path, map_location=map_location)
    if payload.get("format") != "godot-coder-checkpoint":
        raise ValueError("unsupported checkpoint format")
    if payload.get("format_version") != CHECKPOINT_FORMAT_VERSION:
        raise ValueError("unsupported checkpoint version")
    return payload
