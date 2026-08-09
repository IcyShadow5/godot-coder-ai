from __future__ import annotations

import hashlib
import json
import os
import shutil
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np
import torch

from .tokenizer import TokenizerLike

DATA_FORMAT_VERSION = 2
_SUPPORTED_DTYPES = {"uint16": np.dtype("uint16"), "uint32": np.dtype("uint32")}


def _stable_fraction(text: str) -> float:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _token_dtype(vocab_size: int) -> np.dtype:
    if vocab_size <= np.iinfo(np.uint16).max:
        return np.dtype("uint16")
    if vocab_size <= np.iinfo(np.uint32).max:
        return np.dtype("uint32")
    raise ValueError("tokenizer vocabulary exceeds uint32 token storage")


def discover_files(input_dir: Path, extensions: Sequence[str]) -> list[Path]:
    normalized = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}
    files = [path for path in input_dir.rglob("*") if path.is_file() and path.suffix.lower() in normalized]
    return sorted(files, key=lambda path: path.as_posix().lower())


def split_files(files: list[Path], input_dir: Path, val_ratio: float) -> tuple[list[Path], list[Path]]:
    if not 0.0 < val_ratio < 1.0:
        raise ValueError("val_ratio must be between 0 and 1")
    if len(files) < 2:
        raise ValueError("at least two source files are required for train/validation splitting")
    train: list[Path] = []
    val: list[Path] = []
    for path in files:
        relative = path.relative_to(input_dir).as_posix()
        (val if _stable_fraction(relative) < val_ratio else train).append(path)
    if not val:
        val.append(train.pop(-1))
    if not train:
        train.append(val.pop(0))
    return train, val


def _encode_document(path: Path, input_dir: Path, tokenizer: TokenizerLike) -> tuple[str, np.ndarray] | None:
    """Encode one file. Returns None for files that cannot be read safely so the
    caller can skip them without aborting the entire dataset preparation."""
    relative = path.relative_to(input_dir).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        warnings.warn(f"Skipping unreadable file {relative}: {exc}")
        return None
    if "\ufffd" in text:
        warnings.warn(f"File {relative} contains Unicode replacement characters; data may be corrupted")
    framed = f"<file_sep>\n# file: {relative}\n{text.rstrip()}\n"
    ids = tokenizer.encode(framed, add_bos=True, add_eos=True)
    if ids and (min(ids) < 0 or max(ids) >= tokenizer.vocab_size):
        raise ValueError(f"tokenizer produced an out-of-range id for {relative}")
    return relative, np.asarray(ids, dtype=_token_dtype(tokenizer.vocab_size))


def encode_files(files: list[Path], input_dir: Path, tokenizer: TokenizerLike, *, dtype: np.dtype | None = None) -> np.ndarray:
    storage_dtype = dtype or _token_dtype(tokenizer.vocab_size)
    documents = []
    skipped = 0
    for path in files:
        result = _encode_document(path, input_dir, tokenizer)
        if result is None:
            skipped += 1
            continue
        documents.append(result[1].astype(storage_dtype, copy=False))
    if skipped:
        warnings.warn(f"Skipped {skipped} unreadable file(s) during encoding")
    return np.concatenate(documents) if documents else np.asarray([], dtype=storage_dtype)


def _explicit_split_files(source: Path, extensions: Sequence[str]) -> dict[str, list[Path]] | None:
    split_dirs = {name: source / name for name in ("train", "val", "test")}
    if not (split_dirs["train"].is_dir() and split_dirs["val"].is_dir()):
        return None
    result = {name: discover_files(path, extensions) if path.is_dir() else [] for name, path in split_dirs.items()}
    if not result["train"] or not result["val"]:
        raise ValueError("explicit split folders require at least one source file in train/ and val/")
    return result


def _atomic_array_write(path: Path, array: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    array.tofile(temporary)
    os.replace(temporary, path)


def _atomic_json_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _write_split_shards(
    destination: Path,
    split: str,
    files: list[Path],
    source: Path,
    tokenizer: TokenizerLike,
    dtype: np.dtype,
    shard_tokens: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]], int]:
    shards: list[dict[str, object]] = []
    documents: list[dict[str, object]] = []
    current: list[np.ndarray] = []
    current_tokens = 0
    shard_index = 0
    global_offset = 0

    def flush() -> None:
        nonlocal current, current_tokens, shard_index
        if not current:
            return
        array = np.concatenate(current).astype(dtype, copy=False)
        name = f"{split}-{shard_index:05d}.bin"
        path = destination / name
        _atomic_array_write(path, array)
        shards.append({"path": name, "tokens": int(array.size), "sha256": _file_sha256(path)})
        shard_index += 1
        current = []
        current_tokens = 0

    skipped_files: list[str] = []
    for path in files:
        result = _encode_document(path, source, tokenizer)
        if result is None:
            skipped_files.append(path.relative_to(source).as_posix())
            continue
        relative, ids = result
        if current and current_tokens + len(ids) > shard_tokens:
            flush()
        local_offset = current_tokens
        documents.append({
            "path": relative,
            "shard": shard_index,
            "offset": local_offset,
            "global_offset": global_offset,
            "tokens": int(len(ids)),
            "sha256": hashlib.sha256(ids.tobytes()).hexdigest(),
        })
        current.append(ids.astype(dtype, copy=False))
        current_tokens += len(ids)
        global_offset += len(ids)
    if skipped_files:
        warnings.warn(f"Skipped {len(skipped_files)} unreadable file(s) in {split} split")
    flush()
    return shards, documents, global_offset


def prepare_dataset(
    input_dir: str | Path,
    output_dir: str | Path,
    tokenizer: TokenizerLike,
    *,
    val_ratio: float = 0.15,
    extensions: Sequence[str] = (".gd",),
    shard_tokens: int = 8_000_000,
    sampling_policy: str = "packed_with_file_sep",
) -> dict[str, object]:
    source = Path(input_dir).resolve()
    destination = Path(output_dir).resolve()
    if not source.is_dir():
        raise FileNotFoundError(source)
    if shard_tokens < 1024:
        raise ValueError("shard_tokens must be at least 1024")
    if sampling_policy not in {"packed_with_file_sep", "document"}:
        raise ValueError("sampling_policy must be packed_with_file_sep or document")
    backup = destination.with_name(destination.name + ".previous")
    if backup.exists() and not destination.exists():
        # A previous run crashed between the two swap renames; bring the last
        # good dataset back before building a new one.
        backup.replace(destination)
    destination.mkdir(parents=True, exist_ok=True)

    explicit = _explicit_split_files(source, extensions)
    if explicit is None:
        files = discover_files(source, extensions)
        if not files:
            raise FileNotFoundError(f"no matching source files found under {source}")
        train_files, val_files = split_files(files, source, val_ratio)
        test_files: list[Path] = []
        split_mode = "stable-hash"
    else:
        train_files, val_files, test_files = explicit["train"], explicit["val"], explicit["test"]
        split_mode = "folders"

    # Build the entire new dataset (shards, aliases, manifest) inside the
    # staging directory first, then swap it in with a backup + rollback so a
    # failure never leaves a half-deleted dataset behind.
    staging = destination.with_name(destination.name + ".building")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    dtype = _token_dtype(tokenizer.vocab_size)
    split_files_map = {"train": train_files, "val": val_files, "test": test_files}
    split_meta: dict[str, dict[str, object]] = {}
    for split, files in split_files_map.items():
        if not files and split == "test":
            split_meta[split] = {"tokens": 0, "files": [], "shards": [], "documents": []}
            continue
        shards, documents, tokens = _write_split_shards(staging, split, files, source, tokenizer, dtype, shard_tokens)
        split_meta[split] = {
            "tokens": tokens,
            "files": [path.relative_to(source).as_posix() for path in files],
            "shards": shards,
            "documents": documents,
        }

    # Compatibility aliases live inside staging and move with the swap.
    for split, meta in split_meta.items():
        alias = staging / f"{split}.bin"
        if meta["shards"] and len(meta["shards"]) == 1:
            shard_path = staging / str(meta["shards"][0]["path"])
            try:
                os.link(shard_path, alias)
            except OSError:
                # Release the mapping explicitly: an unclosed memmap keeps a
                # file handle until GC, which can block later Windows rebuilds.
                mapped = np.memmap(shard_path, dtype=dtype, mode="r")
                try:
                    _atomic_array_write(alias, mapped)
                finally:
                    del mapped

    fingerprint_payload = {
        "tokenizer": tokenizer.fingerprint(),
        "dtype": dtype.name,
        "split_mode": split_mode,
        "sampling_policy": sampling_policy,
        "splits": {
            split: [item["sha256"] for item in meta["shards"]]
            for split, meta in split_meta.items()
        },
    }
    dataset_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest: dict[str, object] = {
        "format": "godot-coder-token-stream",
        "format_version": DATA_FORMAT_VERSION,
        "dtype": dtype.name,
        "split_mode": split_mode,
        "sampling_policy": sampling_policy,
        "shard_tokens_target": shard_tokens,
        "tokenizer_fingerprint": tokenizer.fingerprint(),
        "dataset_fingerprint": dataset_fingerprint,
        "vocab_size": tokenizer.vocab_size,
        "extensions": list(extensions),
        "splits": split_meta,
    }
    for split, meta in split_meta.items():
        manifest[f"{split}_tokens"] = meta["tokens"]
        manifest[f"{split}_files"] = meta["files"]
        manifest[f"{split}_sha256"] = hashlib.sha256(
            "".join(str(item["sha256"]) for item in meta["shards"]).encode("ascii")
        ).hexdigest() if meta["shards"] else None
    _atomic_json_write(staging / "manifest.json", manifest)

    # Swap the staged dataset into place with a backup + rollback, mirroring
    # corpus._replace_directory. The manifest is part of the swap, so the old
    # dataset stays consistent and loadable until the very last rename.
    backup = destination.with_name(destination.name + ".previous")
    if backup.exists():
        if destination.exists():
            shutil.rmtree(backup)  # a previous swap already completed; drop the stale copy
        else:
            backup.replace(destination)  # a previous swap crashed mid-way; restore it
    if destination.exists():
        destination.replace(backup)
    try:
        staging.replace(destination)
    except Exception:
        if backup.exists() and not destination.exists():
            backup.replace(destination)
        raise
    if backup.exists():
        shutil.rmtree(backup)
    return manifest


@dataclass
class TokenStream:
    """A lazily-loaded shard of tokenised training files with its manifest.

    Use TokenStream.from_data_dir(data_dir, split) to load a prepared split.
    __getitem__ returns (input, target) tensors moved to the requested device.
    """
    path: Path | None
    dtype: np.dtype
    expected_tokens: int | None = None
    shard_paths: list[Path] = field(default_factory=list)
    documents: list[dict[str, object]] = field(default_factory=list)
    sampling_policy: str = "packed_with_file_sep"
    dataset_fingerprint: str | None = None

    @classmethod
    def from_data_dir(cls, data_dir: str | Path, split: str) -> "TokenStream":
        directory = Path(data_dir)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format") != "godot-coder-token-stream":
            raise ValueError("unsupported dataset format")
        version = int(manifest.get("format_version", 1))
        dtype_name = str(manifest.get("dtype"))
        if dtype_name not in _SUPPORTED_DTYPES:
            raise ValueError(f"unsupported token dtype: {dtype_name}")
        if split not in {"train", "val", "test"}:
            raise ValueError(f"unsupported split: {split}")
        expected = int(manifest.get(f"{split}_tokens", 0))
        if version == 1:
            path = directory / f"{split}.bin"
            if not path.exists():
                raise FileNotFoundError(f"missing split: {split}")
            return cls(path=path, dtype=_SUPPORTED_DTYPES[dtype_name], expected_tokens=expected,
                       dataset_fingerprint=manifest.get("dataset_fingerprint"))
        if version != DATA_FORMAT_VERSION:
            raise ValueError("unsupported dataset format version")
        split_meta = (manifest.get("splits") or {}).get(split) or {}
        shard_paths = [directory / str(item["path"]) for item in split_meta.get("shards", [])]
        if not shard_paths:
            raise FileNotFoundError(f"missing split: {split}")
        return cls(
            path=None,
            dtype=_SUPPORTED_DTYPES[dtype_name],
            expected_tokens=expected,
            shard_paths=shard_paths,
            documents=list(split_meta.get("documents", [])),
            sampling_policy=str(manifest.get("sampling_policy", "packed_with_file_sep")),
            dataset_fingerprint=manifest.get("dataset_fingerprint"),
        )

    def __post_init__(self) -> None:
        paths = self.shard_paths or ([self.path] if self.path is not None else [])
        self.shards: list[np.memmap] = []
        for path in paths:
            if path is None or not path.exists():
                raise FileNotFoundError(path)
            size = path.stat().st_size
            if size % self.dtype.itemsize != 0:
                raise ValueError(f"corrupt token stream size for {path}")
            self.shards.append(np.memmap(path, dtype=self.dtype, mode="r"))
        self.shard_offsets = np.cumsum([0] + [len(shard) for shard in self.shards], dtype=np.int64)
        self.tokens = self.shards[0] if len(self.shards) == 1 else _MultiShardView(self.shards, self.shard_offsets)
        if self.expected_tokens is not None and len(self) != self.expected_tokens:
            raise ValueError(f"token stream length mismatch: expected {self.expected_tokens}, got {len(self)}")

    def __del__(self) -> None:
        """Release memmap file handles to avoid Windows file-locking issues."""
        if hasattr(self, "shards"):
            del self.shards

    def __len__(self) -> int:
        return int(self.shard_offsets[-1])

    def _sample_from_shard(self, shard: np.memmap, batch_count: int, seq_len: int, generator: np.random.Generator) -> tuple[list[np.ndarray], list[np.ndarray]]:
        max_start = len(shard) - seq_len - 1
        starts = generator.integers(0, max_start + 1, size=batch_count)
        xs = [np.asarray(shard[i:i + seq_len], dtype=np.int64) for i in starts]
        ys = [np.asarray(shard[i + 1:i + seq_len + 1], dtype=np.int64) for i in starts]
        return xs, ys

    def sample_batch(self, batch_size: int, seq_len: int, device: torch.device, generator: np.random.Generator) -> tuple[torch.Tensor, torch.Tensor]:
        if batch_size <= 0 or seq_len <= 0:
            raise ValueError("batch_size and seq_len must be positive")
        eligible = [index for index, shard in enumerate(self.shards) if len(shard) > seq_len + 1]
        if not eligible:
            raise ValueError(f"dataset has no shard with more than {seq_len + 1} tokens")
        weights = np.asarray([len(self.shards[index]) - seq_len - 1 for index in eligible], dtype=np.float64)
        weights /= weights.sum()
        choices = generator.choice(eligible, size=batch_size, p=weights)
        xs: list[np.ndarray] = []
        ys: list[np.ndarray] = []
        for shard_index in choices:
            x_parts, y_parts = self._sample_from_shard(self.shards[int(shard_index)], 1, seq_len, generator)
            xs.extend(x_parts); ys.extend(y_parts)
        x = torch.from_numpy(np.stack(xs)); y = torch.from_numpy(np.stack(ys))
        if device.type == "cuda":
            x = x.pin_memory().to(device, non_blocking=True)
            y = y.pin_memory().to(device, non_blocking=True)
        else:
            x = x.to(device); y = y.to(device)
        return x, y

    def fixed_windows(self, seq_len: int, count: int, seed: int) -> np.ndarray:
        if count <= 0:
            raise ValueError("count must be positive")
        rng = np.random.default_rng(seed)
        eligible = [index for index, shard in enumerate(self.shards) if len(shard) > seq_len + 1]
        if not eligible:
            raise ValueError("validation split is too short for configured context")
        result = np.empty((count, 2), dtype=np.int64)
        weights = np.asarray([len(self.shards[index]) - seq_len - 1 for index in eligible], dtype=np.float64)
        weights /= weights.sum()
        for row in range(count):
            shard_index = int(rng.choice(eligible, p=weights))
            start = int(rng.integers(0, len(self.shards[shard_index]) - seq_len))
            result[row] = (shard_index, start)
        return result

    def batch_at(self, windows: np.ndarray, seq_len: int, device: torch.device) -> tuple[torch.Tensor, torch.Tensor]:
        xs = [np.asarray(self.shards[int(shard)][int(start):int(start) + seq_len], dtype=np.int64) for shard, start in windows]
        ys = [np.asarray(self.shards[int(shard)][int(start) + 1:int(start) + seq_len + 1], dtype=np.int64) for shard, start in windows]
        x = torch.from_numpy(np.stack(xs)); y = torch.from_numpy(np.stack(ys))
        if device.type == "cuda":
            x = x.pin_memory().to(device, non_blocking=True); y = y.pin_memory().to(device, non_blocking=True)
        else:
            x = x.to(device); y = y.to(device)
        return x, y


class _MultiShardView:
    def __init__(self, shards: list[np.memmap], offsets: np.ndarray) -> None:
        self.shards, self.offsets = shards, offsets
    def __len__(self) -> int:
        return int(self.offsets[-1])
    @property
    def size(self) -> int:
        return len(self)
