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
from godot_coder.progress_events import EVENT_PREFIX, ProgressEmitter, parse_event_line


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


def test_fixed_evaluation_windows_cleans_stale_variants(tmp_path: Path) -> None:
    """A changed eval config must replace the cache, not pile up .npy files."""
    def fake_fixed_windows(seq_len, count, seed):
        return np.zeros((count, 2), dtype=np.int64)

    stream = SimpleNamespace(dataset_fingerprint="abc", fixed_windows=fake_fixed_windows)
    first_config = TrainConfig(eval_batches=2, batch_size=4, evaluation_seed=7)
    stale = train._eval_cache_path(tmp_path, stream, first_config, _model_config())
    train.fixed_evaluation_windows(tmp_path, stream, first_config, _model_config())
    assert stale.exists()

    second_config = TrainConfig(eval_batches=3, batch_size=4, evaluation_seed=7)
    train.fixed_evaluation_windows(tmp_path, stream, second_config, _model_config())
    # The old variant for the same dataset fingerprint is gone, and exactly
    # one cache file remains.
    assert not stale.exists()
    assert len(list((tmp_path / "eval_cache").glob("fixed_abc_*.npy"))) == 1


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


def test_train_loss_progress_event_round_trip() -> None:
    """The live-loss event train.py emits must round-trip through the parser."""
    sink: list[str] = []
    emitter = ProgressEmitter(job_id="job123", sink=sink.append)
    emitter.emit(
        "train_loss",
        step=5,
        loss=0.5,
        learning_rate=1e-4,
        gradient_norm=0.12,
        tokens_per_second=1234.5,
    )
    assert sink and sink[0].startswith(EVENT_PREFIX)
    event = parse_event_line(sink[0], job_id="job123")
    assert event is not None
    assert event["event"] == "train_loss"
    assert event["step"] == 5
    assert event["loss"] == 0.5
    assert event["learning_rate"] == 1e-4
    assert event["gradient_norm"] == 0.12
    assert event["tokens_per_second"] == 1234.5
    assert event["job_id"] == "job123"


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


def test_interrupt_requested_detects_stop_file(tmp_path: Path) -> None:
    assert train._interrupt_requested(None) is False
    assert train._interrupt_requested(str(tmp_path / "missing.stop")) is False
    stop = tmp_path / "job.stop"
    stop.write_text("stop", encoding="utf-8")
    assert train._interrupt_requested(str(stop)) is True


def test_interrupt_flag_set_by_signal_handler() -> None:
    train._interrupt_flag.clear()
    assert train._interrupt_requested(None) is False
    train._interrupt_flag.set()
    assert train._interrupt_requested(None) is True
    train._interrupt_flag.clear()

def test_evaluate_sliding_mode_flushes_tail_on_short_corpus() -> None:
    # Corpus too small to fill the eval budget: the tail windows used to be
    # dropped, which could leave `losses` empty and crash the evaluation.
    model = TinyGPT(_model_config())
    stream = _fake_batch_stream(np.arange(20, dtype=np.int64))
    config = TrainConfig(
        eval_batches=3, batch_size=4, evaluation_mode="sliding",
        evaluation_stride=2, dtype="float32",
    )
    loss = train.evaluate(
        model, stream, config, _model_config(),
        torch.device("cpu"), np.random.default_rng(0),
        lambda: torch.no_grad(),
    )
    assert loss > 0


def test_evaluate_sliding_mode_respects_window_budget() -> None:
    # Sliding must consume eval_batches * batch_size windows like fixed mode,
    # not just eval_batches windows.
    seen: dict[str, int] = {"n": 0}

    class CountingStream:
        shards = [np.arange(512, dtype=np.int64)]
        dataset_fingerprint = "stream"

        def batch_at(self, windows, seq_len, device):
            rows = [self.shards[int(s)][int(st):int(st) + seq_len] for s, st in np.asarray(windows, dtype=np.int64)]
            x = torch.from_numpy(np.stack(rows)).to(device)
            seen["n"] += len(rows)
            return x, x.roll(-1, dims=1)

    model = TinyGPT(_model_config())
    config = TrainConfig(
        eval_batches=3, batch_size=2, evaluation_mode="sliding",
        evaluation_stride=8, dtype="float32",
    )
    train.evaluate(
        model, CountingStream(), config, _model_config(),
        torch.device("cpu"), np.random.default_rng(0),
        lambda: torch.no_grad(),
    )
    assert seen["n"] == 3 * 2


def _prefetch_stream():
    class _Stream:
        shards = [np.arange(64, dtype=np.int64)]
        dataset_fingerprint = "stream"

        def sample_batch(self, batch_size, seq_len, device, generator):
            starts = generator.integers(0, 64 - seq_len, size=batch_size)
            xs = [self.shards[0][start:start + seq_len] for start in starts]
            x = torch.from_numpy(np.stack(xs)).to(device)
            return x, x.roll(-1, dims=1)

    return _Stream()


def test_prefetch_matches_non_prefetch_sequence() -> None:
    # Prefetch must not change what the model sees: the worker draws from the
    # same training RNG as the sync path, so the batch sequence is identical
    # either way, and a resume continues the stream instead of replaying it.
    def batches(enabled: bool) -> list[list[int]]:
        rng = np.random.default_rng(42)
        prefetcher = train.BatchPrefetcher(_prefetch_stream(), 2, 8, rng, enabled=enabled)
        try:
            return [prefetcher.next(torch.device("cpu"))[0].tolist() for _ in range(3)]
        finally:
            prefetcher.close()

    assert batches(True) == batches(False)


def test_prefetch_first_batch_matches_sync_draw_and_advances_shared_rng() -> None:
    # The first batch comes from the shared training RNG, so it is the very
    # first sync draw: the checkpoint state a resume relies on tracks the
    # stream position instead of standing still while prefetch runs.
    rng = np.random.default_rng(11)
    initial = np.asarray(rng.bit_generator.state["state"]["state"]).copy()
    prefetcher = train.BatchPrefetcher(_prefetch_stream(), 2, 8, rng, enabled=True)
    try:
        first = prefetcher.next(torch.device("cpu"))[0].tolist()
        moved = np.asarray(rng.bit_generator.state["state"]["state"])
        assert not np.array_equal(initial, moved)
    finally:
        prefetcher.close()

    sync_rng = np.random.default_rng(11)
    prefetcher = train.BatchPrefetcher(_prefetch_stream(), 2, 8, sync_rng, enabled=False)
    try:
        sync_first = prefetcher.next(torch.device("cpu"))[0].tolist()
    finally:
        prefetcher.close()
    assert first == sync_first


def test_prefetch_rng_state_matches_sync_position() -> None:
    # The checkpoint snapshot must sit at the same stream position as a
    # sync run after the same number of batches: rng_state() is taken when
    # the batch is handed over, so a resume continues the same sequence.
    def prefetched_state() -> tuple[dict[str, object], list[list[int]]]:
        rng = np.random.default_rng(23)
        prefetcher = train.BatchPrefetcher(_prefetch_stream(), 2, 8, rng, enabled=True)
        try:
            batches = [prefetcher.next(torch.device("cpu"))[0].tolist() for _ in range(3)]
            return prefetcher.rng_state(), batches
        finally:
            prefetcher.close()

    def sync_state() -> tuple[dict[str, object], list[list[int]]]:
        rng = np.random.default_rng(23)
        prefetcher = train.BatchPrefetcher(_prefetch_stream(), 2, 8, rng, enabled=False)
        try:
            batches = [prefetcher.next(torch.device("cpu"))[0].tolist() for _ in range(3)]
            return rng.bit_generator.state, batches
        finally:
            prefetcher.close()

    prefetched, p_batches = prefetched_state()
    plain, s_batches = sync_state()
    assert p_batches == s_batches
    # default_rng() is PCG64, whose position is the 64-bit counter in
    # 'state' plus 'inc' -- no MT19937-style 'pos'. Same consumption
    # pattern, so all three must line up between the two paths.
    assert int(prefetched["state"]["inc"]) == int(plain["state"]["inc"])
    assert np.array_equal(
        np.asarray(prefetched["state"]["state"]),
        np.asarray(plain["state"]["state"]),
    )


def test_prefetch_sequence_is_deterministic_per_seed() -> None:
    def first_batches() -> list[list[int]]:
        rng = np.random.default_rng(7)
        prefetcher = train.BatchPrefetcher(_prefetch_stream(), 2, 8, rng, enabled=True)
        try:
            return [prefetcher.next(torch.device("cpu"))[0].tolist() for _ in range(3)]
        finally:
            prefetcher.close()

    assert first_batches() == first_batches()


def test_prefetch_sequence_depends_on_seed() -> None:
    # Two prefetchers from the same seed draw the same stream; a different
    # seed diverges. The sequence comes from the RNG, not from shared state.
    def batches(seed: int) -> list[list[int]]:
        rng = np.random.default_rng(seed)
        prefetcher = train.BatchPrefetcher(_prefetch_stream(), 2, 8, rng, enabled=True)
        try:
            return [prefetcher.next(torch.device("cpu"))[0].tolist() for _ in range(2)]
        finally:
            prefetcher.close()

    assert batches(3) == batches(3)
    assert batches(3) != batches(4)


def test_snapshot_with_fallback_prefers_prefetcher_state() -> None:
    prefetched = {"source": "prefetcher"}
    fallback = {"source": "fallback"}
    assert train._snapshot_with_fallback(lambda: prefetched, fallback) is prefetched


def test_snapshot_with_fallback_uses_raw_rng_when_prefetcher_missing() -> None:
    rng = np.random.default_rng(5)
    rng.integers(0, 100)
    fallback = rng.bit_generator.state

    def missing_prefetcher() -> dict[str, object]:
        raise NameError("name 'prefetcher' is not defined")

    state = train._snapshot_with_fallback(missing_prefetcher, fallback)
    assert state is fallback


def test_snapshot_with_fallback_survives_prefetcher_errors() -> None:
    def broken() -> dict[str, object]:
        raise RuntimeError("worker hung")

    fallback = {"ok": True}
    assert train._snapshot_with_fallback(broken, fallback) is fallback


def test_snapshot_with_fallback_handles_none_prefetcher() -> None:
    # The finally guard binds prefetcher to None before the try, so a
    # construction failure surfaces as AttributeError (None.rng_state),
    # not the NameError of a truly unbound name.
    rng = np.random.default_rng(9)
    rng.integers(0, 100)
    fallback = rng.bit_generator.state

    def none_prefetcher() -> dict[str, object]:
        return None.rng_state()

    state = train._snapshot_with_fallback(none_prefetcher, fallback)
    assert state is fallback
