from __future__ import annotations
"""Checkpoint save/load with atomic writes, hard-link aliases, and RNG state capture."""

import os
import pickle
import random
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import torch

# Intentionally stays at 1: the RNG state layout changed (raw numpy tuple ->
# primitive dict) but _coerce_numpy_state reads both, so bumping the version
# would only hard-break every existing checkpoint for no reason.
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
    np.random.set_state(_coerce_numpy_state(state["numpy"]))
    torch_state = state["torch"]
    if isinstance(torch_state, torch.Tensor):
        # Resuming loads the whole checkpoint with map_location=device, which
        # puts the CPU RNG state on CUDA. torch.set_rng_state only accepts a
        # CPU ByteTensor, so bring it back home first.
        torch_state = torch_state.detach().cpu()
    else:
        # Legacy checkpoints kept the torch state as a plain list of ints.
        torch_state = torch.tensor(torch_state, dtype=torch.uint8)
    torch.set_rng_state(torch_state)
    if torch.cuda.is_available() and "cuda" in state:
        # Same story as the CPU state: set_rng_state_all expects CPU ByteTensors
        # and copies them over lazily, so a map_location=cuda load must be
        # brought back to CPU first.
        cuda_states = [s.detach().cpu() if isinstance(s, torch.Tensor) else s for s in state["cuda"]]
        torch.cuda.set_rng_state_all(cuda_states)


def _rng_state_to_primitives(state: dict[str, Any]) -> dict[str, Any]:
    """Flatten RNG state into JSON-safe values so checkpoints load safely.

    The numpy MT19937 key is an ndarray, the one object torch's safe unpickler
    rejects. A plain list of ints loads under weights_only=True with no
    allowlist at all; the other RNG pieces are already primitives.
    """
    bit_generator, key_array, pos, has_gauss, cached_gaussian = state["numpy"]
    primitive = dict(state)
    primitive["numpy"] = {
        "bit_generator": bit_generator,
        "key": key_array.tolist(),
        "pos": pos,
        "has_gauss": has_gauss,
        "cached_gaussian": cached_gaussian,
    }
    return primitive


def _coerce_numpy_state(numpy_state: Any) -> Any:
    """Accept the live tuple, the legacy tuple, or the primitive dict on disk."""
    if not isinstance(numpy_state, dict):
        return numpy_state  # live capture or legacy checkpoint
    key = numpy_state["key"]
    if isinstance(key, list):
        key = np.asarray(key, dtype=np.uint32)
    return (
        numpy_state["bit_generator"],
        key,
        numpy_state["pos"],
        numpy_state["has_gauss"],
        numpy_state["cached_gaussian"],
    )


def _legacy_numpy_globals() -> list[Any]:
    """Numpy's array machinery needed to rebuild legacy RNG keys.

    Only numpy's own reconstruct/ndarray/dtype classes are admitted; every other
    global stays blocked by weights_only=True.
    """
    try:
        from numpy._core import multiarray as _multiarray  # numpy 2+
    except ImportError:  # pragma: no cover - numpy 1.x module layout
        from numpy.core import multiarray as _multiarray  # type: ignore[no-redef]
    globals_list: list[Any] = [np.ndarray, np.dtype, _multiarray._reconstruct]
    try:
        import numpy.dtypes as _dtypes  # numpy 2+ dtype classes

        globals_list.extend(
            getattr(_dtypes, name)
            for name in dir(_dtypes)
            if name.endswith("DType") and isinstance(getattr(_dtypes, name), type)
        )
    except ImportError:  # pragma: no cover - numpy 1.x
        pass
    return globals_list


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
        "rng_state": _rng_state_to_primitives(capture_rng_state()),
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
        payload = torch.load(checkpoint_path, map_location=map_location, weights_only=True)
    except pickle.UnpicklingError:
        # Legacy checkpoints stored the numpy RNG key as an ndarray - the one
        # object the safe unpickler rejects. Retry with exactly numpy's array
        # machinery allowlisted; anything else is still blocked.
        with torch.serialization.safe_globals(_legacy_numpy_globals()):
            payload = torch.load(checkpoint_path, map_location=map_location, weights_only=True)
    if payload.get("format") != "godot-coder-checkpoint":
        raise ValueError("unsupported checkpoint format")
    if payload.get("format_version") != CHECKPOINT_FORMAT_VERSION:
        raise ValueError("unsupported checkpoint version")
    return payload
