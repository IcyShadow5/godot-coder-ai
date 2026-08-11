from __future__ import annotations

from pathlib import Path

import json
import threading
import time

import torch

from godot_coder.process_control import ManagedProcessResult
from godot_coder.sampling import DEFAULT_REPETITION_PENALTY, DEFAULT_TEMPERATURE, DEFAULT_TOP_K, DEFAULT_TOP_P
from godot_coder.ui.schemas import GenerateRequest
from godot_coder.ui.services import GenerationService, system_status, validate_code


def _seed_project(root: Path) -> Path:
    project = root / "data" / "raw" / "seed_project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "project.godot").write_text('config_version=5\n[application]\nconfig/name="Seed"\n', encoding="utf-8")
    return project


def test_validate_code_runs_godot_through_managed_process(tmp_path: Path, monkeypatch) -> None:
    """Chat validation must use run_managed_process so a hung Mono-Godot child
    is killed as a tree, not left behind after the 30s timeout."""
    _seed_project(tmp_path)
    monkeypatch.setattr("godot_coder.ui.services.find_godot", lambda: "fake-godot")

    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((list(command), kwargs))
        return ManagedProcessResult(
            command=list(command),
            return_code=1,
            output="SCRIPT ERROR: Parse Error: Expected parameter name.",
            timed_out=False,
            duration_seconds=0.2,
            pid=4321,
            termination_attempted=False,
        )

    monkeypatch.setattr("godot_coder.ui.services.run_managed_process", fake_run)

    result = validate_code(tmp_path, "extends Node\nfunc broken(\n")
    assert result["passed"] is False
    assert result["return_code"] == 1
    assert "Parse Error" in result["output"]
    assert result["timed_out"] is False
    assert calls and calls[0][1]["timeout_seconds"] == 30
    # The temp script is always cleaned up, even on failure.
    assert not list((tmp_path / "data" / "generated").glob("studio_validation_*.gd"))


def test_validate_code_reports_timeout_flag(tmp_path: Path, monkeypatch) -> None:
    _seed_project(tmp_path)
    monkeypatch.setattr("godot_coder.ui.services.find_godot", lambda: "fake-godot")

    def fake_run(command, **kwargs):
        return ManagedProcessResult(
            command=list(command),
            return_code=None,
            output="still busy",
            timed_out=True,
            duration_seconds=30.0,
            pid=4321,
            termination_attempted=True,
        )

    monkeypatch.setattr("godot_coder.ui.services.run_managed_process", fake_run)

    result = validate_code(tmp_path, "extends Node\n")
    assert result["timed_out"] is True
    assert result["return_code"] is None


def test_validate_code_records_real_duration(tmp_path: Path, monkeypatch) -> None:
    """The parse metrics must carry the measured duration, not a fixed 30s."""
    _seed_project(tmp_path)
    monkeypatch.setattr("godot_coder.ui.services.find_godot", lambda: "fake-godot")
    records: list[tuple[object, dict[str, object]]] = []

    def fake_record(collector, event, /, **fields):
        records.append((event, fields))
        return None

    monkeypatch.setattr("godot_coder.metrics.MetricsCollector.record", fake_record)
    monkeypatch.setattr(
        "godot_coder.ui.services.run_managed_process",
        lambda command, **kwargs: ManagedProcessResult(
            command=list(command),
            return_code=0,
            output="ok",
            timed_out=False,
            duration_seconds=3.7,
            pid=1,
            termination_attempted=False,
        ),
    )
    validate_code(tmp_path, "extends Node\n")
    assert records
    assert records[0][1]["duration_seconds"] == 3.7


def test_generate_request_defaults_follow_sampling() -> None:
    """Studio chat defaults must come from sampling.py, not drift independently."""
    request = GenerateRequest(checkpoint="x", prompt="p")
    assert request.temperature == DEFAULT_TEMPERATURE
    assert request.top_k == DEFAULT_TOP_K
    assert request.top_p == DEFAULT_TOP_P
    assert request.repetition_penalty == DEFAULT_REPETITION_PENALTY


class _FakeTokenizer:
    vocab_size = 269
    eos_id = 0

    def __init__(self) -> None:
        self.decoded_ids: list[int] | None = None

    def fingerprint(self) -> str:
        return "fp-123"

    def encode(self, text: str, add_bos: bool = True, add_eos: bool = False) -> list[int]:
        return [10, 11, 12]  # 3 prompt tokens

    def decode(self, ids: list[int], skip_special_tokens: bool = True) -> str:
        self.decoded_ids = list(ids)
        return "extends Node\nfunc generated():\n\tpass"


class _FakeModel:
    def __init__(self, config) -> None:
        self.config = config

    def to(self, device):
        return self

    def eval(self) -> None:
        pass

    def load_state_dict(self, state) -> None:
        pass

    def generate_stream(self, input_ids, **kwargs):
        # The service only uses the streaming API now: 3 prompt tokens, then
        # the two new completion tokens yielded one at a time.
        yield torch.tensor([[99]])
        yield torch.tensor([[100]])


def test_generate_returns_only_completion_not_prompt(tmp_path: Path, monkeypatch) -> None:
    """The chat shows the prompt already; generated text must not echo it back."""
    (tmp_path / "checkpoints" / "v06").mkdir(parents=True)
    (tmp_path / "checkpoints" / "v06" / "best.pt").write_bytes(b"dummy")
    (tmp_path / "artifacts").mkdir()

    payload = {
        "train_config": {"tokenizer_path": "artifacts/tokenizer.json"},
        "tokenizer_fingerprint": "fp-123",
        "model_config": {
            "vocab_size": 269,
            "max_seq_len": 32,
            "n_layers": 1,
            "d_model": 32,
            "n_heads": 4,
            "d_ff": 64,
            "dropout": 0.0,
            "rope_base": 10000.0,
            "tie_embeddings": True,
            "gradient_checkpointing": False,
        },
        "model_state": {},
    }
    monkeypatch.setattr("godot_coder.ui.services.load_checkpoint", lambda path, map_location: payload)
    tokenizer = _FakeTokenizer()
    monkeypatch.setattr("godot_coder.ui.services.load_tokenizer", lambda path: tokenizer)
    monkeypatch.setattr("godot_coder.ui.services.TinyGPT", _FakeModel)
    monkeypatch.setattr("godot_coder.ui.services.resolve_device", lambda name: torch.device("cpu"))

    service = GenerationService(tmp_path)
    result = service.generate(
        "checkpoints/v06/best.pt",
        "extends Node",
        max_new_tokens=16,
        temperature=0.8,
        top_k=40,
    )
    assert result.text.startswith("extends Node")  # decode result from the fake tokenizer
    assert result.cancelled is False
    assert tokenizer.decoded_ids == [99, 100], "the prompt prefix must be stripped from the completion"


def test_generate_surfaces_cancelled_flag(tmp_path: Path, monkeypatch) -> None:
    """generate() must surface a cancelled stream, not silently return partial text."""
    service = GenerationService(tmp_path)

    def fake_stream(self, checkpoint_path, prompt, **kwargs):
        yield {"done": True, "text": "partial", "tokens": 1, "cancelled": True}

    monkeypatch.setattr(GenerationService, "generate_stream", fake_stream)
    result = service.generate("checkpoints/v06/best.pt", "extends Node", max_new_tokens=4)
    assert result.text == "partial"
    assert result.cancelled is True

    def fake_stream_finished(self, checkpoint_path, prompt, **kwargs):
        yield {"done": True, "text": "complete", "tokens": 2, "cancelled": False}

    monkeypatch.setattr(GenerationService, "generate_stream", fake_stream_finished)
    finished = service.generate("checkpoints/v06/best.pt", "extends Node", max_new_tokens=4)
    assert finished.text == "complete"
    assert finished.cancelled is False


def test_generate_stream_yields_deltas_then_done(tmp_path: Path, monkeypatch) -> None:
    """The stream emits live deltas first, then a done event with the cleaned text."""
    (tmp_path / "checkpoints" / "v06").mkdir(parents=True)
    (tmp_path / "checkpoints" / "v06" / "best.pt").write_bytes(b"dummy")
    monkeypatch.setattr("godot_coder.ui.services.load_checkpoint", lambda path, map_location: {
        "train_config": {"tokenizer_path": "artifacts/tokenizer.json"},
        "tokenizer_fingerprint": "fp-123",
        "model_config": {"vocab_size": 269, "max_seq_len": 32, "n_layers": 1, "d_model": 32,
                         "n_heads": 4, "d_ff": 64, "dropout": 0.0, "rope_base": 10000.0,
                         "tie_embeddings": True, "gradient_checkpointing": False},
        "model_state": {},
    })
    tokenizer = _FakeTokenizer()
    monkeypatch.setattr("godot_coder.ui.services.load_tokenizer", lambda path: tokenizer)
    monkeypatch.setattr("godot_coder.ui.services.TinyGPT", _FakeModel)
    monkeypatch.setattr("godot_coder.ui.services.resolve_device", lambda name: torch.device("cpu"))

    service = GenerationService(tmp_path)
    events = list(service.generate_stream(
        "checkpoints/v06/best.pt", "extends Node", max_new_tokens=16, temperature=0.8, top_k=40,
    ))
    tokens = [event["token"] for event in events if "token" in event]
    done = [event for event in events if "done" in event][-1]
    assert tokens  # at least one live delta was emitted
    assert done["done"] is True
    assert done["tokens"] == 2
    assert done["text"].startswith("extends Node")
    # Only the completion tokens were decoded, never the prompt prefix.
    assert tokenizer.decoded_ids == [99, 100]


def test_generate_stream_metrics_flag_controls_recording(tmp_path: Path, monkeypatch) -> None:
    """record_metrics=False must skip the metrics file entirely (read-only verify)."""
    (tmp_path / "checkpoints" / "v06").mkdir(parents=True)
    (tmp_path / "checkpoints" / "v06" / "best.pt").write_bytes(b"dummy")
    monkeypatch.setattr("godot_coder.ui.services.load_checkpoint", lambda path, map_location: {
        "train_config": {"tokenizer_path": "artifacts/tokenizer.json"},
        "tokenizer_fingerprint": "fp-123",
        "model_config": {"vocab_size": 269, "max_seq_len": 32, "n_layers": 1, "d_model": 32,
                         "n_heads": 4, "d_ff": 64, "dropout": 0.0, "rope_base": 10000.0,
                         "tie_embeddings": True, "gradient_checkpointing": False},
        "model_state": {},
    })
    monkeypatch.setattr("godot_coder.ui.services.load_tokenizer", lambda path: _FakeTokenizer())
    monkeypatch.setattr("godot_coder.ui.services.TinyGPT", _FakeModel)
    monkeypatch.setattr("godot_coder.ui.services.resolve_device", lambda name: torch.device("cpu"))

    recorded: list[object] = []

    class SpyMetrics:
        def __init__(self, path) -> None:
            pass

        def record(self, event, **kwargs) -> None:
            recorded.append((event, kwargs))

    monkeypatch.setattr("godot_coder.ui.services.MetricsCollector", SpyMetrics)

    service = GenerationService(tmp_path)
    list(service.generate_stream("checkpoints/v06/best.pt", "extends Node", max_new_tokens=4, record_metrics=False))
    assert recorded == []
    # The Studio default keeps recording as before.
    list(service.generate_stream("checkpoints/v06/best.pt", "extends Node", max_new_tokens=4))
    assert recorded


def test_generate_stream_falls_back_to_versioned_tokenizer(tmp_path: Path, monkeypatch) -> None:
    """A checkpoint whose tokenizer file was rebuilt still loads via the versioned sibling."""
    (tmp_path / "checkpoints" / "v06").mkdir(parents=True)
    (tmp_path / "checkpoints" / "v06" / "best.pt").write_bytes(b"dummy")
    (tmp_path / "artifacts").mkdir()
    # The versioned sibling the resolver looks for must exist on disk.
    (tmp_path / "artifacts" / "tokenizer_fp-123.json").write_bytes(b"dummy")
    monkeypatch.setattr("godot_coder.ui.services.load_checkpoint", lambda path, map_location: {
        "train_config": {"tokenizer_path": "artifacts/tokenizer.json"},
        "tokenizer_fingerprint": "fp-123",
        "model_config": {"vocab_size": 269, "max_seq_len": 32, "n_layers": 1, "d_model": 32,
                         "n_heads": 4, "d_ff": 64, "dropout": 0.0, "rope_base": 10000.0,
                         "tie_embeddings": True, "gradient_checkpointing": False},
        "model_state": {},
    })

    class _MismatchedTokenizer:
        def fingerprint(self) -> str:
            return "fp-other"

    # The configured file no longer matches the checkpoint; the versioned sibling does.
    monkeypatch.setattr("godot_coder.ui.services.load_tokenizer", lambda path: _MismatchedTokenizer())
    monkeypatch.setattr("godot_coder.tokenizer.load_tokenizer", lambda path: _FakeTokenizer())
    monkeypatch.setattr("godot_coder.ui.services.TinyGPT", _FakeModel)
    monkeypatch.setattr("godot_coder.ui.services.resolve_device", lambda name: torch.device("cpu"))

    service = GenerationService(tmp_path)
    events = list(service.generate_stream("checkpoints/v06/best.pt", "extends Node", max_new_tokens=4))
    assert any("done" in event for event in events)


def test_generate_accepts_temperature_and_top_k_bounds(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "checkpoints" / "v06").mkdir(parents=True)
    (tmp_path / "checkpoints" / "v06" / "best.pt").write_bytes(b"dummy")
    monkeypatch.setattr("godot_coder.ui.services.load_checkpoint", lambda path, map_location: {
        "train_config": {"tokenizer_path": "artifacts/tokenizer.json"},
        "tokenizer_fingerprint": "fp-123",
        "model_config": {"vocab_size": 269, "max_seq_len": 32, "n_layers": 1, "d_model": 32,
                         "n_heads": 4, "d_ff": 64, "dropout": 0.0, "rope_base": 10000.0,
                         "tie_embeddings": True, "gradient_checkpointing": False},
        "model_state": {},
    })
    monkeypatch.setattr("godot_coder.ui.services.load_tokenizer", lambda path: _FakeTokenizer())
    monkeypatch.setattr("godot_coder.ui.services.TinyGPT", _FakeModel)
    monkeypatch.setattr("godot_coder.ui.services.resolve_device", lambda name: torch.device("cpu"))

    service = GenerationService(tmp_path)
    # Boundary values must not raise.
    service.generate("checkpoints/v06/best.pt", "extends Node", max_new_tokens=1, temperature=0.0, top_k=0)
    service.generate("checkpoints/v06/best.pt", "extends Node", max_new_tokens=4096, temperature=5.0, top_k=1000)


def _autotune_report(root: Path, available: bool, reason: str | None = None) -> None:
    report = root / "reports" / "hardware" / "autotune_latest.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        '{"compile_available": %s, "compile_disabled_reason": %s}'
        % (str(available).lower(), json.dumps(reason)),
        encoding="utf-8",
    )


def _quiet_system_status(project_root: Path, static: tuple[bool, str | None], monkeypatch):
    monkeypatch.setattr("godot_coder.ui.services._compile_status", lambda: static)
    monkeypatch.setattr("godot_coder.ui.services.find_godot", lambda: None)
    return system_status(project_root)


def test_system_status_compile_falls_back_without_autotune(tmp_path, monkeypatch) -> None:
    """No autotune report yet -> the static import signal decides."""
    status = _quiet_system_status(tmp_path, (True, "3.7.1"), monkeypatch)
    assert status["compile_available"] is True
    assert status["compile_disabled_reason"] is None


def test_system_status_autotune_probe_overrides_static(tmp_path, monkeypatch) -> None:
    """The probe proved compile fails - panel must say so despite the static check."""
    _autotune_report(tmp_path, False, "kernel build failed")
    status = _quiet_system_status(tmp_path, (True, "3.7.1"), monkeypatch)
    assert status["compile_available"] is False
    assert status["compile_disabled_reason"] == "kernel build failed"


def test_system_status_autotune_true_preferred_over_static_false(tmp_path, monkeypatch) -> None:
    """A fresh probe that proved compile works wins over a static no."""
    _autotune_report(tmp_path, True)
    status = _quiet_system_status(tmp_path, (False, None), monkeypatch)
    assert status["compile_available"] is True
    assert status["compile_disabled_reason"] is None


def test_system_status_malformed_autotune_does_not_crash(tmp_path, monkeypatch) -> None:
    """Broken or unreadable report must fall back silently."""
    report = tmp_path / "reports" / "hardware" / "autotune_latest.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("{not json", encoding="utf-8")
    status = _quiet_system_status(tmp_path, (True, "3.7.1"), monkeypatch)
    assert status["compile_available"] is True
    assert status["compile_disabled_reason"] is None


def test_system_status_autotune_naive_iso_timestamp_does_not_crash(tmp_path, monkeypatch) -> None:
    """A naive ISO created_at used to raise TypeError (offset-naive
    minus offset-aware datetime) and take down system_status."""
    report = tmp_path / "reports" / "hardware" / "autotune_latest.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(
            {
                "compile_available": False,
                "compile_disabled_reason": "kernel build failed",
                "created_at": "2026-08-01T10:00:00",
            }
        ),
        encoding="utf-8",
    )
    status = _quiet_system_status(tmp_path, (True, "3.7.1"), monkeypatch)
    # The naive timestamp is unreadable, so the probe verdict is
    # ignored and the static signal decides.
    assert status["compile_available"] is True
    assert status["compile_disabled_reason"] is None


def test_system_status_autotune_true_with_static_true_stays_true(tmp_path, monkeypatch) -> None:
    """Both signals agree: probe proved it and triton imports now."""
    _autotune_report(tmp_path, True)
    status = _quiet_system_status(tmp_path, (True, "3.7.1"), monkeypatch)
    assert status["compile_available"] is True
    assert status["compile_disabled_reason"] is None


def test_system_status_stale_probe_falls_back_to_static(tmp_path, monkeypatch) -> None:
    """An old failed probe must not override a now-working static check."""
    from datetime import datetime, timedelta, timezone

    report = tmp_path / "reports" / "hardware" / "autotune_latest.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    report.write_text(
        '{"created_at": %s, "compile_available": false, "compile_disabled_reason": "old failure"}'
        % json.dumps(old),
        encoding="utf-8",
    )
    status = _quiet_system_status(tmp_path, (True, "3.7.1"), monkeypatch)
    assert status["compile_available"] is True
    assert status["compile_disabled_reason"] is None


def _unload_setup(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "checkpoints" / "v06").mkdir(parents=True)
    (tmp_path / "checkpoints" / "v06" / "best.pt").write_bytes(b"dummy")
    monkeypatch.setattr("godot_coder.ui.services.load_checkpoint", lambda path, map_location: {
        "train_config": {"tokenizer_path": "artifacts/tokenizer.json"},
        "tokenizer_fingerprint": "fp-123",
        "model_config": {"vocab_size": 269, "max_seq_len": 32, "n_layers": 1, "d_model": 32,
                         "n_heads": 4, "d_ff": 64, "dropout": 0.0, "rope_base": 10000.0,
                         "tie_embeddings": True, "gradient_checkpointing": False},
        "model_state": {},
    })
    tokenizer = _FakeTokenizer()
    monkeypatch.setattr("godot_coder.ui.services.load_tokenizer", lambda path: tokenizer)
    monkeypatch.setattr("godot_coder.ui.services.resolve_device", lambda name: torch.device("cpu"))
    # The fake checkpoint payload carries an empty model_state; without a
    # TinyGPT mock the real model would reject it on load.
    monkeypatch.setattr("godot_coder.ui.services.TinyGPT", _FakeModel)


def test_unload_requests_cancel_and_stream_stops_early(tmp_path: Path, monkeypatch) -> None:
    """unload() must never move the model under a running stream's forward.

    Instead of blocking until the stream ends, unload() marks the current
    stream generation; the stream sees the marker at its next token step,
    stops early, and delivers a done event flagged ``cancelled``. The lock
    is then released so unload() can swap the model out - the wait is one
    token step, not the whole completion.
    """
    _unload_setup(tmp_path, monkeypatch)

    release = threading.Event()

    class _BlockingModel(_FakeModel):
        def generate_stream(self, input_ids, **kwargs):
            yield torch.tensor([[99]])
            release.wait(2.0)  # pause inside the stream, lock still held
            yield torch.tensor([[100]])

    monkeypatch.setattr("godot_coder.ui.services.TinyGPT", _BlockingModel)

    service = GenerationService(tmp_path)
    events = service.generate_stream("checkpoints/v06/best.pt", "extends Node", max_new_tokens=4)
    first = next(events)
    assert "context" in first
    second = next(events)
    assert "token" in second  # model is now paused in the forward pass

    unload_done = threading.Event()

    def _unload() -> None:
        service.unload()
        unload_done.set()

    unloader = threading.Thread(target=_unload, daemon=True)
    unloader.start()
    # Poll until the marker is armed (unload set it before blocking on the
    # lock); only then the stream is guaranteed to stop at its next step.
    deadline = time.time() + 1.0
    while service._cancel_seq is None and time.time() < deadline:
        time.sleep(0.01)
    assert service._cancel_seq == service._stream_seq, "cancel marker was not armed"
    assert not unload_done.is_set(), "unload() must still wait for the in-flight token step"

    release.set()  # let the paused forward finish; the stream must stop here
    done = None
    for event in events:
        if "done" in event:
            done = event
            break
    assert done is not None, "a cancelled stream must still end with a done event"
    assert done["cancelled"] is True
    assert done["tokens"] == 1  # the second token was never accumulated

    # The done event is terminal; a streaming consumer stops here, so close
    # the generator to release the lock before unload() can proceed.
    events.close()
    unloader.join(timeout=2.0)
    assert unload_done.is_set(), "unload() must complete after the stream stops"


def test_unload_without_active_stream_does_not_cancel_next_stream(tmp_path: Path, monkeypatch) -> None:
    """The cancel marker is scoped to the stream generation, not sticky.

    unload() called while no stream runs must not poison a stream that
    starts afterwards: it takes its own, newer generation number and runs
    to completion with ``cancelled`` False.
    """
    _unload_setup(tmp_path, monkeypatch)

    service = GenerationService(tmp_path)
    service.unload()  # no stream active - the marker must not stick

    events = service.generate_stream("checkpoints/v06/best.pt", "extends Node", max_new_tokens=4)
    done = None
    for event in events:
        if "done" in event:
            done = event
            break
    assert done is not None
    assert done["cancelled"] is False
    assert done["tokens"] == 2  # both fake-model tokens were accumulated
    events.close()
