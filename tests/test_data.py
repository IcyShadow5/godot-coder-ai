from pathlib import Path

import numpy as np
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
