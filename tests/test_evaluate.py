"""Tests for the evaluate CLI — checkpoint loss measurement on val/test data."""

import json
import sys
from types import SimpleNamespace

import pytest
import torch

import godot_coder.evaluate as evaluate


class _FakeTokenizer:
    def __init__(self) -> None:
        self.eos_id = 0

    def fingerprint(self) -> str:
        return "abc"

    def encode(self, text, *, add_bos=False):
        return [1, 2, 3]

    def decode(self, ids, *, skip_special_tokens=False):
        return "x"


class _FakeModel:
    def __init__(self, config) -> None:
        self.config = config

    def to(self, device):
        return self

    def load_state_dict(self, state) -> None:
        self.state = state

    def eval(self) -> None:
        self.evaluated = True

    def __call__(self, x, y):
        return SimpleNamespace(loss=torch.tensor(0.5))


class _FakeStream:
    def __init__(self, batches):
        self._batches = batches

    def sample_batch(self, batch_size, max_seq_len, device, rng):
        return (
            torch.zeros((batch_size, max_seq_len), dtype=torch.long),
            torch.zeros((batch_size, max_seq_len), dtype=torch.long),
        )


_MODEL_CONFIG = {
    "vocab_size": 269,
    "max_seq_len": 32,
    "n_layers": 4,
    "d_model": 192,
    "n_heads": 6,
    "d_ff": 512,
    "dropout": 0.0,
    "rope_base": 10000.0,
    "tie_embeddings": True,
    "gradient_checkpointing": False,
}


def _install_fakes(monkeypatch, tmp_path):
    checkpoint = tmp_path / "latest.pt"
    checkpoint.write_bytes(b"fake")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "manifest.json").write_text(
        json.dumps({"format": "godot-coder-token-stream", "tokenizer_fingerprint": "abc"}),
        encoding="utf-8",
    )
    payload = {
        "train_config": {
            "tokenizer_path": "artifacts/tokenizer.json",
            "data_dir": "data/processed",
            "batch_size": 2,
            "dtype": "float32",
        },
        "tokenizer_fingerprint": "abc",
        "model_config": dict(_MODEL_CONFIG),
        "model_state": {"w": 1},
    }
    monkeypatch.setattr(evaluate, "find_project_root", lambda start: tmp_path)
    monkeypatch.setattr(evaluate, "resolve_project_path", lambda root, path: data_dir)
    monkeypatch.setattr(evaluate, "load_checkpoint", lambda path, map_location: payload)
    monkeypatch.setattr(evaluate, "load_tokenizer", lambda path: _FakeTokenizer())
    monkeypatch.setattr(evaluate, "TinyGPT", _FakeModel)
    monkeypatch.setattr(evaluate, "resolve_device", lambda device: torch.device("cpu"))
    monkeypatch.setattr(evaluate, "TokenStream", type("TS", (), {"from_data_dir": staticmethod(lambda d, s: _FakeStream(1))}))
    monkeypatch.setattr(evaluate, "amp_settings", lambda device, dtype: (False, None))
    monkeypatch.setattr(evaluate, "project_relative", lambda path, root: "latest.pt")
    return checkpoint, data_dir


def _run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["evaluate"] + argv)
    evaluate.main()


def test_rejects_non_positive_batches(monkeypatch, tmp_path):
    checkpoint, _ = _install_fakes(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="batches"):
        _run_main(monkeypatch, ["--checkpoint", str(checkpoint), "--batches", "0"])


def test_measure_loss_prints_json(monkeypatch, tmp_path, capsys):
    checkpoint, _ = _install_fakes(monkeypatch, tmp_path)
    _run_main(monkeypatch, ["--checkpoint", str(checkpoint), "--batches", "2", "--batch-size", "1"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["split"] == "val"
    assert payload["batches"] == 2
    assert payload["loss"] == pytest.approx(0.5)
    assert payload["perplexity"] == pytest.approx(1.648721271, rel=1e-3)
    assert payload["tokens"] == 2 * 1 * 32
    assert payload["tokens_per_second"] > 0


def test_test_split_allowed(monkeypatch, tmp_path, capsys):
    checkpoint, _ = _install_fakes(monkeypatch, tmp_path)
    _run_main(monkeypatch, ["--checkpoint", str(checkpoint), "--split", "test", "--batches", "1"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["split"] == "test"
