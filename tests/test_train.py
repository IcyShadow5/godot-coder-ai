"""Unit tests for the train.py helpers that do not need a GPU run."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from godot_coder import train
from godot_coder.config import ModelConfig, TrainConfig
from godot_coder.model import TinyGPT


def _model_config() -> ModelConfig:
    return ModelConfig(vocab_size=269, max_seq_len=16, n_layers=1, d_model=32, n_heads=4, d_ff=64)


def test_parse_args_defaults(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["train"])
    args = train.parse_args()
    assert args.config == "configs/tiny.yaml"
    assert args.resume is None
    assert args.max_steps is None


def test_parse_args_overrides(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["train", "--config", "c.yaml", "--resume", "r.pt", "--max-steps", "50"],
    )
    args = train.parse_args()
    assert args.config == "c.yaml"
    assert args.resume == "r.pt"
    assert args.max_steps == 50


def test_set_seeds_is_deterministic() -> None:
    train.set_seeds(1234)
    first_np = np.random.randint(0, 1_000_000)
    first_torch = torch.randint(0, 1_000_000, (1,)).item()
    train.set_seeds(1234)
    assert np.random.randint(0, 1_000_000) == first_np
    assert torch.randint(0, 1_000_000, (1,)).item() == first_torch


def test_learning_rate_warmup_linear() -> None:
    config = TrainConfig(learning_rate=1e-3, min_learning_rate=0.0, warmup_steps=100)
    assert train.learning_rate(0, config, max_steps=1000) == pytest.approx(1e-3 / 100)
    assert train.learning_rate(49, config, max_steps=1000) == pytest.approx(1e-3 * 0.5)
    assert train.learning_rate(99, config, max_steps=1000) == pytest.approx(1e-3)


def test_learning_rate_cosine_decay_to_floor() -> None:
    config = TrainConfig(learning_rate=1e-3, min_learning_rate=0.0, warmup_steps=100)
    halfway = train.learning_rate(100 + (1000 - 100) // 2, config, max_steps=1000)
    assert halfway == pytest.approx(5e-4, abs=1e-6)
    # past the end the progress is capped at 1.0 -> exactly min_learning_rate
    assert train.learning_rate(1000, config, max_steps=1000) == pytest.approx(0.0, abs=1e-12)


def test_make_optimizer_two_groups() -> None:
    model = TinyGPT(_model_config())
    config = TrainConfig(weight_decay=0.1, learning_rate=1e-3)
    optimizer = train.make_optimizer(model, config, torch.device("cpu"))
    assert len(optimizer.param_groups) == 2
    assert optimizer.param_groups[0]["weight_decay"] == 0.1
    assert optimizer.param_groups[1]["weight_decay"] == 0.0


def test_amp_settings_non_cuda() -> None:
    assert train.amp_settings(torch.device("cpu"), "float32") == (False, None)
    assert train.amp_settings(torch.device("cpu"), "float16") == (False, None)
    assert train.amp_settings(torch.device("cpu"), "bfloat16") == (False, None)


def test_amp_settings_cuda_float32_stays_off() -> None:
    assert train.amp_settings(torch.device("cuda"), "float32") == (False, None)


def test_atomic_json_writes_and_leaves_no_tmp(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "data.json"
    train._atomic_json(target, {"a": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
    assert not list(tmp_path.rglob("*.tmp"))


def test_append_jsonl_appends_lines(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    train._append_jsonl(path, {"step": 1})
    train._append_jsonl(path, {"step": 2})
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert [json.loads(line)["step"] for line in lines] == [1, 2]


def test_file_sha256_matches_hashlib(tmp_path: Path) -> None:
    payload = b"hello" * 1000
    path = tmp_path / "blob.bin"
    path.write_bytes(payload)
    assert train._file_sha256(path) == hashlib.sha256(payload).hexdigest()


def test_profile_metadata_parses_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "prof.yaml"
    config_path.write_text("profile:\n  id: tiny\n  label: Tiny\n", encoding="utf-8")
    assert train._profile_metadata(config_path) == {"id": "tiny", "label": "Tiny"}


def test_profile_metadata_missing_returns_empty(tmp_path: Path) -> None:
    assert train._profile_metadata(tmp_path / "missing.yaml") == {}


def test_project_root_for_configs_folder(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir(parents=True)
    assert train._project_root_for(configs / "tiny.yaml") == tmp_path.resolve()


def test_require_under_ok_and_rejected(tmp_path: Path) -> None:
    inside = tmp_path / "a.txt"
    inside.write_text("x", encoding="utf-8")
    train._require_under(inside, tmp_path, "config")
    outside = tmp_path.parent / "b.txt"
    with pytest.raises(ValueError):
        train._require_under(outside, tmp_path, "config")


def test_eval_cache_path_name(tmp_path: Path) -> None:
    stream = SimpleNamespace(dataset_fingerprint="abcdef1234567890")
    config = TrainConfig(eval_batches=2, batch_size=4, evaluation_seed=7)
    path = train._eval_cache_path(tmp_path, stream, config, _model_config())
    assert "abcdef1234567890" in path.name
    assert path.parent.name == "eval_cache"


def test_fixed_evaluation_windows_caches(tmp_path: Path) -> None:
    windows = np.zeros((8, 2), dtype=np.int64)
    calls = {"n": 0}

    def fake_fixed_windows(seq_len, count, seed):
        calls["n"] += 1
        assert count == 8
        return windows.copy()

    stream = SimpleNamespace(dataset_fingerprint="abc", fixed_windows=fake_fixed_windows)
    config = TrainConfig(eval_batches=2, batch_size=4, evaluation_seed=7)
    first = train.fixed_evaluation_windows(tmp_path, stream, config, _model_config())
    second = train.fixed_evaluation_windows(tmp_path, stream, config, _model_config())
    assert first.shape == (8, 2)
    assert second.shape == (8, 2)
    assert calls["n"] == 1  # second call loads the saved cache instead of recomputing


def test_evaluate_sample_mode_on_cpu() -> None:
    model = TinyGPT(_model_config())

    class FakeStream:
        shards = []
        dataset_fingerprint = "x"

        def sample_batch(self, batch_size, seq_len, device, rng):
            x = torch.zeros((batch_size, seq_len), dtype=torch.long)
            return x, x

    config = TrainConfig(eval_batches=2, batch_size=2, evaluation_mode="sample", dtype="float32")
    loss = train.evaluate(
        model,
        FakeStream(),
        config,
        _model_config(),
        torch.device("cpu"),
        np.random.default_rng(0),
        lambda: torch.no_grad(),
    )
    assert loss > 0


def _fake_batch_stream(shard: np.ndarray):
    """Minimal TokenStream stand-in with a working batch_at (x + shifted y)."""

    class _Stream:
        shards = [shard]
        dataset_fingerprint = "stream"

        def batch_at(self, windows, seq_len, device):
            rows = [
                self.shards[int(s)][int(st):int(st) + seq_len]
                for s, st in np.asarray(windows, dtype=np.int64)
            ]
            x = torch.from_numpy(np.stack(rows)).to(device)
            return x, x.roll(-1, dims=1)

    return _Stream()


def test_evaluate_fixed_mode_requires_windows() -> None:
    model = TinyGPT(_model_config())
    stream = _fake_batch_stream(np.arange(64, dtype=np.int64))
    config = TrainConfig(eval_batches=1, batch_size=1, evaluation_mode="fixed", dtype="float32")
    with pytest.raises(ValueError):
        train.evaluate(
            model, stream, config, _model_config(),
            torch.device("cpu"), np.random.default_rng(0),
            lambda: torch.no_grad(),
        )


def test_evaluate_fixed_mode_uses_cached_windows() -> None:
    model = TinyGPT(_model_config())
    stream = _fake_batch_stream(np.arange(64, dtype=np.int64))
    windows = np.array([[0, 0], [0, 8]], dtype=np.int64)
    config = TrainConfig(eval_batches=2, batch_size=2, evaluation_mode="fixed", dtype="float32")
    loss = train.evaluate(
        model, stream, config, _model_config(),
        torch.device("cpu"), np.random.default_rng(0),
        lambda: torch.no_grad(), fixed_windows=windows,
    )
    assert loss > 0


def test_evaluate_sliding_mode_runs_with_stride() -> None:
    model = TinyGPT(_model_config())
    stream = _fake_batch_stream(np.arange(64, dtype=np.int64))
    config = TrainConfig(
        eval_batches=3, batch_size=2, evaluation_mode="sliding",
        evaluation_stride=8, dtype="float32",
    )
    loss = train.evaluate(
        model, stream, config, _model_config(),
        torch.device("cpu"), np.random.default_rng(0),
        lambda: torch.no_grad(),
    )
    assert loss > 0
