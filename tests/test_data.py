from pathlib import Path

import numpy as np
import pytest
import torch

from godot_coder.data import TokenStream, prepare_dataset
from godot_coder.tokenizer import ByteTokenizer


def test_prepare_and_sample(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    raw.mkdir()
    for index in range(4):
        (raw / f"script_{index}.gd").write_text(
            f"extends Node\nvar value: int = {index}\n" * 20,
            encoding="utf-8",
        )
    tokenizer = ByteTokenizer()
    manifest = prepare_dataset(raw, processed, tokenizer, val_ratio=0.25)
    assert manifest["train_tokens"] > 0
    assert manifest["val_tokens"] > 0
    stream = TokenStream.from_data_dir(processed, "train")
    x, y = stream.sample_batch(2, 16, torch.device("cpu"), np.random.default_rng(1))
    assert x.shape == y.shape == (2, 16)


def test_prepare_respects_explicit_split_folders(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    for split, count in (("train", 3), ("val", 2), ("test", 1)):
        directory = raw / split
        directory.mkdir(parents=True)
        for index in range(count):
            (directory / f"{split}_{index}.gd").write_text(
                f"extends Node\nvar value: int = {index}\n" * 20,
                encoding="utf-8",
            )
    manifest = prepare_dataset(raw, processed, ByteTokenizer())
    assert manifest["split_mode"] == "folders"
    assert len(manifest["train_files"]) == 3
    assert len(manifest["val_files"]) == 2
    assert len(manifest["test_files"]) == 1
    assert (processed / "test.bin").exists()
    test_stream = TokenStream.from_data_dir(processed, "test")
    assert len(test_stream) > 0


class _WideTokenizer:
    vocab_size = 70_000
    pad_id = 0
    bos_id = 1
    eos_id = 2
    file_sep_id = 3

    def encode(self, text: str, *, add_bos: bool = False, add_eos: bool = False) -> list[int]:
        ids = [69_999, 65_536, 42]
        return ([self.bos_id] if add_bos else []) + ids + ([self.eos_id] if add_eos else [])

    def decode(self, ids, *, skip_special_tokens: bool = False) -> str:
        return "wide"

    def fingerprint(self) -> str:
        return "wide-tokenizer"

    def save(self, path) -> None:
        Path(path).write_text("{}", encoding="utf-8")


def test_prepare_data_cli_exits_cleanly_with_piped_stdout(tmp_path: Path, monkeypatch) -> None:
    """Regression: prepare_data used to print the whole manifest (~10MB with
    per-document metadata) to stdout. On a pipe nobody drains, that print
    blocks forever and the job looks "finished but frozen". It must print a
    short summary instead and exit promptly.
    """
    import os
    import subprocess
    import sys
    import time

    from godot_coder.tokenizer import ByteTokenizer

    src = tmp_path / "raw"; src.mkdir()
    for index in range(6):
        (src / f"s{index}.gd").write_text(f"extends Node\nfunc f{index}():\n\treturn {index}\n" * 300, encoding="utf-8")
    out = tmp_path / "out"
    tokenizer_path = tmp_path / "tok.json"
    ByteTokenizer().save(tokenizer_path)

    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, "-u", "-m", "godot_coder.prepare_data",
         "--input", str(src), "--output", str(out), "--tokenizer", str(tokenizer_path),
         "--val-ratio", "0.15"],
        capture_output=True, text=True, timeout=60, env=env,
    )
    elapsed = time.monotonic() - started

    assert completed.returncode == 0, completed.stderr
    assert elapsed < 30, "prepare_data must exit quickly, not hang on a stdout dump"
    assert "Prepared " in completed.stdout
    assert "split(s):" in completed.stdout
    # The giant per-document dump is gone: summary output stays small.
    assert len(completed.stdout) < 500
    assert (out / "manifest.json").exists()


def test_prepare_uses_uint32_for_wide_vocabularies(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    raw.mkdir()
    for index in range(2):
        (raw / f"wide_{index}.gd").write_text("extends Node\n", encoding="utf-8")
    manifest = prepare_dataset(raw, processed, _WideTokenizer(), val_ratio=0.5)
    assert manifest["dtype"] == "uint32"
    stream = TokenStream.from_data_dir(processed, "train")
    assert stream.dtype == np.dtype("uint32")
    assert max(stream.tokens) == 69_999


def test_encoding_resilience_gracefully_skips_bad_files(tmp_path: Path) -> None:
    """UTF-8 BOM works, invalid bytes skipped, prepare_dataset does not crash."""
    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    raw.mkdir()
    # UTF-8 BOM (EF BB BF) followed by valid content
    (raw / "bom.gd").write_bytes(
        bytes([0xEF, 0xBB, 0xBF]) + b"extends Node\nfunc hello(): pass\n" * 10
    )
    # Invalid UTF-8 bytes (0xFC is never valid in UTF-8)
    (raw / "bad.gd").write_bytes(
        b"extends Node\n# Pr" + bytes([0xFC]) + b"fung\n" * 5
    )
    # Normal UTF-8
    (raw / "ok.gd").write_text("extends Node\nfunc hello(): pass\n" * 10, encoding="utf-8")

    tokenizer = ByteTokenizer()
    manifest = prepare_dataset(raw, processed, tokenizer, val_ratio=0.5)
    # Should have processed at least the valid files without crashing
    assert manifest["train_tokens"] > 0
    assert len(manifest["train_files"]) >= 2  # bom.gd + ok.gd (bad.gd skipped)



def test_prepare_dataset_restores_previous_shards_on_failed_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure while swapping shards must keep the previous dataset intact."""
    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    raw.mkdir()
    for index in range(4):
        (raw / f"script_{index}.gd").write_text(
            f"extends Node\nvar value: int = {index}\n" * 20, encoding="utf-8"
        )
    tokenizer = ByteTokenizer()
    prepare_dataset(raw, processed, tokenizer, val_ratio=0.25)
    old_manifest = (processed / "manifest.json").read_bytes()
    old_shards = {p.name: p.read_bytes() for p in processed.glob("*.bin")}

    # One more file so the second run stages a different dataset.
    (raw / "extra.gd").write_text("extends Node\nvar extra: int = 1\n" * 20, encoding="utf-8")

    real_path_replace = Path.replace

    def flaky_replace(self, target, *args, **kwargs):
        # Fail exactly when the staged dataset moves into place, mid-swap.
        if self.name.endswith(".building"):
            raise OSError("simulated failure while swapping shards")
        return real_path_replace(self, target, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    with pytest.raises(OSError):
        prepare_dataset(raw, processed, tokenizer, val_ratio=0.25)

    # The old dataset came back and still loads.
    assert (processed / "manifest.json").read_bytes() == old_manifest
    for name, content in old_shards.items():
        assert (processed / name).read_bytes() == content
    assert len(TokenStream.from_data_dir(processed, "train")) > 0


def test_prepare_dataset_recovers_from_leftover_backup(tmp_path: Path) -> None:
    """A leftover .previous directory from a crashed swap is restored first."""
    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    raw.mkdir()
    for index in range(4):
        (raw / f"script_{index}.gd").write_text(
            f"extends Node\nvar value: int = {index}\n" * 20, encoding="utf-8"
        )
    tokenizer = ByteTokenizer()
    prepare_dataset(raw, processed, tokenizer, val_ratio=0.25)
    assert (processed / "manifest.json").exists()

    # Simulate a crash between the two swap renames: the dataset dir is gone
    # and only the .previous backup remains.
    backup = processed.with_name(processed.name + ".previous")
    processed.rename(backup)

    (raw / "extra.gd").write_text("extends Node\nvar extra: int = 1\n" * 20, encoding="utf-8")
    prepare_dataset(raw, processed, tokenizer, val_ratio=0.25)

    assert not backup.exists()
    assert (processed / "manifest.json").exists()
    assert len(TokenStream.from_data_dir(processed, "train")) > 0
