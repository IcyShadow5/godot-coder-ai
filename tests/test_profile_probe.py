"""Tests for profile_probe.py - the VRAM/throughput probe CLI.

Covers the pure helpers (_parameter_memory, _is_oom_error, _batch_candidates,
_recommendation), config parsing (_read_profile), a real tiny CPU worker run
(_worker_result) plus its OOM / compile / failure branches, the subprocess
wrapper (_run_worker) with mocked subprocess, the report orchestration
(run_probe) and the worker mode of main(). No real GPU or Godot is needed.
"""

import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from godot_coder.profile_probe import (
    _RESULT_PREFIX,
    _batch_candidates,
    _is_oom_error,
    _model_weight_dtype,
    _parameter_memory,
    _read_profile,
    _recommendation,
    _run_worker,
    _worker_result,
    main,
    parse_args,
    run_probe,
)

CONFIG_YAML = """\
profile:
  id: test
  title: Test Profile
  beginner_order: 3
  risk: medium
model:
  max_seq_len: 16
  n_layers: 1
  d_model: 32
  n_heads: 4
  d_ff: 64
  dropout: 0.0
  rope_base: 10000.0
  tie_embeddings: true
  gradient_checkpointing: false
train:
  tokenizer_path: artifacts/tokenizer.json
  data_dir: data/processed
  output_dir: checkpoints/test
  device: cpu
  dtype: float32
  seed: 1
  batch_size: 2
  gradient_accumulation_steps: 1
  max_steps: 1
  learning_rate: 0.001
  min_learning_rate: 0.0001
  warmup_steps: 0
  weight_decay: 0.0
  beta1: 0.9
  beta2: 0.95
  gradient_clip: 1.0
  log_interval: 1
  eval_interval: 1
  eval_batches: 1
  save_interval: 1
  compile_model: false
"""


def _scaffold(root: Path) -> None:
    (root / "configs").mkdir(parents=True)
    (root / "artifacts").mkdir()
    (root / "checkpoints").mkdir()
    (root / "data" / "processed").mkdir(parents=True)


def _write_config(root: Path, name: str = "night.yaml", *, compile_model: bool = False, profile_extra: str = "", profile_id: str = "test") -> Path:
    body = CONFIG_YAML.replace("  compile_model: false", f"  compile_model: {str(compile_model).lower()}")
    body = body.replace("  id: test", f"  id: {profile_id}")
    if profile_extra:
        body = body.replace("  risk: medium", profile_extra, 1)
    path = root / "configs" / name
    path.write_text(body, encoding="utf-8")
    return path


class _OomModel(torch.nn.Module):
    """TinyGPT stand-in whose forward always runs out of memory."""

    def __init__(self, config) -> None:
        super().__init__()
        self._param = torch.nn.Parameter(torch.zeros(2))
        self.config = SimpleNamespace(tie_embeddings=False)
        self.token_embedding = SimpleNamespace(weight=torch.zeros(2, 2))

    def to(self, device):
        return self

    def parameters(self):
        return iter([self._param])

    def parameter_count(self) -> int:
        return 2

    def forward(self, x, y):
        raise torch.OutOfMemoryError("CUDA out of memory")


class _BoomModel(torch.nn.Module):
    """TinyGPT stand-in whose forward fails with a non-OOM RuntimeError."""

    def __init__(self, config) -> None:
        super().__init__()
        self._param = torch.nn.Parameter(torch.zeros(2))
        self.config = SimpleNamespace(tie_embeddings=False)
        self.token_embedding = SimpleNamespace(weight=torch.zeros(2, 2))

    def to(self, device):
        return self

    def parameters(self):
        return iter([self._param])

    def parameter_count(self) -> int:
        return 2

    def forward(self, x, y):
        raise RuntimeError("boom")


# --- parse_args --------------------------------------------------------------


def test_parse_args_defaults_and_flags(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["probe", "--root", "C:/lab", "--max-batch", "6"])
    args = parse_args()
    assert args.root == "C:/lab"
    assert args.config == []
    assert args.max_batch == 6
    assert args.warmup_steps == 1
    assert args.measure_steps == 2


def test_parse_args_repeated_config(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["probe", "--config", "a.yaml", "--config", "b.yaml"])
    args = parse_args()
    assert args.config == ["a.yaml", "b.yaml"]


# --- _read_profile -----------------------------------------------------------


def test_read_profile_valid(tmp_path):
    _scaffold(tmp_path)
    path = _write_config(tmp_path)
    profile, model, train = _read_profile(path)
    assert profile["id"] == "test"
    assert model.max_seq_len == 16
    assert train.batch_size == 2


def test_read_profile_probe_vocab_size_override(tmp_path):
    _scaffold(tmp_path)
    path = _write_config(tmp_path, profile_extra="  probe_vocab_size: 256")
    _, model, _ = _read_profile(path)
    assert model.vocab_size == 256


def test_read_profile_tokenizer_sets_vocab(tmp_path, monkeypatch):
    _scaffold(tmp_path)
    path = _write_config(tmp_path)
    (tmp_path / "artifacts" / "tokenizer.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "godot_coder.profile_probe.load_tokenizer",
        lambda p: SimpleNamespace(vocab_size=777),
    )
    _, model, _ = _read_profile(path)
    assert model.vocab_size == 777


def test_read_profile_missing_sections_raises(tmp_path):
    _scaffold(tmp_path)
    (tmp_path / "configs" / "bad.yaml").write_text("profile: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="model and train"):
        _read_profile(tmp_path / "configs" / "bad.yaml")


# --- pure helpers ------------------------------------------------------------


def test_parameter_memory_math():
    # 1e9 parameters * 4 bytes = 3.72529... GiB.
    assert _parameter_memory(1_000_000_000, 4) == pytest.approx(3.72529, rel=1e-4)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (torch.OutOfMemoryError("OOM"), True),
        (RuntimeError("cuda error: out of memory"), True),
        (RuntimeError("CUDA out of memory"), True),
        (RuntimeError("out of memory"), True),
        (RuntimeError("boom"), False),
        (ValueError("boom"), False),
    ],
)
def test_is_oom_error(exc, expected):
    assert _is_oom_error(exc) is expected


def test_model_weight_dtype():
    assert _model_weight_dtype(None) is None
    model = torch.nn.Linear(2, 2)
    assert _model_weight_dtype(model) == "float32"
    empty = torch.nn.Module()
    assert _model_weight_dtype(empty) is None


def test_batch_candidates_variants():
    assert _batch_candidates(2, 8) == [1, 2, 4, 6, 8]
    assert _batch_candidates(2, 2) == [1, 2]
    assert _batch_candidates(3, 3) == [1, 2, 3]
    assert _batch_candidates(7, 7) == [1, 2, 4, 6, 7]
    # configured batch above the maximum is dropped.
    assert _batch_candidates(10, 8) == [1, 2, 4, 6, 8]
    # maximum above the ladder is appended.
    assert _batch_candidates(8, 16) == [1, 2, 4, 6, 8, 16]


# --- _recommendation ---------------------------------------------------------


def _passing(profile_id, *, fraction=0.5, beginner_order=1, recommended=False, headroom=3.5):
    return {
        "profile_id": profile_id,
        "recommended_by_design": recommended,
        "beginner_order": beginner_order,
        "configured_result": {
            "status": "pass",
            "peak_reserved_fraction": fraction,
            "headroom_gib": headroom,
        },
    }


def test_recommendation_no_passing():
    profiles = [
        {"profile_id": "a", "configured_result": {"status": "oom"}},
        {"profile_id": "b", "configured_result": {"status": "error"}},
    ]
    rec = _recommendation(profiles)
    assert rec["profile_id"] is None
    assert "None of the profiles passed" in rec["reason"]


def test_recommendation_designed_preferred():
    profiles = [
        _passing("starter", beginner_order=1),
        _passing("balanced", beginner_order=2, recommended=True),
    ]
    rec = _recommendation(profiles)
    assert rec["profile_id"] == "balanced"
    assert "headroom" in rec["reason"]


def test_recommendation_comfortable_max_beginner_order():
    profiles = [
        _passing("starter", beginner_order=1),
        _passing("advanced", beginner_order=5),
        _passing("experimental", beginner_order=9),
    ]
    rec = _recommendation(profiles)
    # All comfortable -> the highest beginner_order wins.
    assert rec["profile_id"] == "experimental"


def test_recommendation_all_above_headroom_min_beginner_order():
    profiles = [
        _passing("starter", fraction=0.95, beginner_order=1),
        _passing("balanced", fraction=0.95, beginner_order=2),
        _passing("experimental", fraction=0.95, beginner_order=9),
    ]
    rec = _recommendation(profiles)
    assert rec["profile_id"] == "starter"


def test_recommendation_experimental_extra_note():
    # Experimental passes but is NOT comfortable (fraction > 0.9), so a
    # comfortable profile is selected and the note about experimental being
    # an edge test must be appended to the reason.
    profiles = [
        _passing("starter", beginner_order=1),
        _passing("experimental", fraction=0.95, beginner_order=9),
    ]
    rec = _recommendation(profiles)
    assert rec["profile_id"] == "starter"
    assert "deliberately remains an edge test" in rec["reason"]


# --- _worker_result ----------------------------------------------------------


def test_worker_result_cpu_pass(tmp_path):
    _scaffold(tmp_path)
    path = _write_config(tmp_path)
    result = _worker_result(path, batch_size=2, device_name="cpu", warmup_steps=0, measure_steps=1)
    assert result["status"] == "pass"
    assert result["batch_size"] == 2
    assert result["parameters"] > 0
    assert result["measured_steps"] == 1
    assert math.isfinite(result["mean_loss"])
    assert result["mean_step_ms"] > 0
    assert result["tokens_per_second"] > 0
    assert result["device"] == "cpu"
    assert result["compile_requested"] is False
    assert result["compile_enabled"] is False
    assert result["compile_error"] is None


def test_worker_result_oom_returns_oom_status(tmp_path, monkeypatch):
    _scaffold(tmp_path)
    path = _write_config(tmp_path)
    monkeypatch.setattr("godot_coder.profile_probe.TinyGPT", _OomModel)
    result = _worker_result(path, batch_size=2, device_name="cpu", warmup_steps=0, measure_steps=1)
    assert result["status"] == "oom"
    assert "out of memory" in result["error"].lower()
    assert result["batch_size"] == 2


def test_worker_result_non_oom_error_reraises(tmp_path, monkeypatch):
    _scaffold(tmp_path)
    path = _write_config(tmp_path)
    monkeypatch.setattr("godot_coder.profile_probe.TinyGPT", _BoomModel)
    with pytest.raises(RuntimeError, match="boom"):
        _worker_result(path, batch_size=2, device_name="cpu", warmup_steps=0, measure_steps=1)


def test_worker_result_compile_success(tmp_path, monkeypatch):
    _scaffold(tmp_path)
    path = _write_config(tmp_path, compile_model=True)
    monkeypatch.setattr(
        "godot_coder.profile_probe.torch.compile",
        lambda model, mode=None, dynamic=None: model,
    )
    result = _worker_result(path, batch_size=2, device_name="cpu", warmup_steps=0, measure_steps=1)
    assert result["status"] == "pass"
    assert result["compile_requested"] is True
    assert result["compile_enabled"] is True
    assert result["compile_error"] is None


def test_worker_result_compile_error_falls_back(tmp_path, monkeypatch):
    _scaffold(tmp_path)
    path = _write_config(tmp_path, compile_model=True)

    def broken_compile(model, mode=None, dynamic=None):
        raise RuntimeError("Triton not installed")

    monkeypatch.setattr("godot_coder.profile_probe.torch.compile", broken_compile)
    result = _worker_result(path, batch_size=2, device_name="cpu", warmup_steps=0, measure_steps=1)
    assert result["status"] == "pass"
    assert result["compile_requested"] is True
    assert result["compile_enabled"] is False
    assert result["compile_error"] == "RuntimeError: Triton not installed"


# --- _run_worker -------------------------------------------------------------


def _fake_subprocess(monkeypatch, *, stdout="", stderr="", returncode=0):
    def fake_run(*a, **k):
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)

    monkeypatch.setattr("godot_coder.profile_probe.subprocess.run", fake_run)
    return fake_run


def test_run_worker_parses_stdout_result(tmp_path, monkeypatch):
    payload = json.dumps({"status": "pass", "batch_size": 4, "tokens_per_second": 123.4})
    _fake_subprocess(monkeypatch, stdout="some log\n" + _RESULT_PREFIX + payload, stderr="")
    result = _run_worker(tmp_path, "configs/night.yaml", 4, "cpu", 1, 2)
    assert result["status"] == "pass"
    assert result["batch_size"] == 4
    assert result["return_code"] == 0


def test_run_worker_parses_stderr_result(tmp_path, monkeypatch):
    payload = json.dumps({"status": "oom", "batch_size": 8})
    _fake_subprocess(monkeypatch, stdout="", stderr=_RESULT_PREFIX + payload)
    result = _run_worker(tmp_path, "configs/night.yaml", 8, "cpu", 1, 2)
    assert result["status"] == "oom"
    assert result["batch_size"] == 8


def test_run_worker_no_result_line_returns_error(tmp_path, monkeypatch):
    _fake_subprocess(monkeypatch, stdout="", stderr="crashed hard", returncode=1)
    result = _run_worker(tmp_path, "configs/night.yaml", 4, "cpu", 1, 2)
    assert result["status"] == "error"
    assert result["return_code"] == 1
    assert "crashed hard" in result["error"]


def test_run_worker_builds_command(tmp_path, monkeypatch):
    captured = {}

    def fake_run(*a, **k):
        captured["command"] = a[0]
        captured["cwd"] = k.get("cwd")
        captured["timeout"] = k.get("timeout")
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("godot_coder.profile_probe.subprocess.run", fake_run)
    _run_worker(tmp_path, "configs/night.yaml", 4, "cpu", 1, 2)
    command = captured["command"]
    assert command[:6] == [sys.executable, "-u", "-m", "godot_coder.profile_probe", "--root", str(tmp_path)]
    assert "--worker" in command
    assert command[command.index("--batch-size") + 1] == "4"
    assert command[command.index("--device") + 1] == "cpu"
    assert command[command.index("--warmup-steps") + 1] == "1"
    assert command[command.index("--measure-steps") + 1] == "2"
    assert captured["cwd"] == tmp_path
    assert captured["timeout"] == 1800


# --- run_probe ---------------------------------------------------------------


def test_run_probe_writes_report_and_recommendation(tmp_path, monkeypatch):
    _scaffold(tmp_path)
    _write_config(tmp_path, "starter.yaml", profile_id="starter", profile_extra="  probe_max_batch_size: 4")
    _write_config(
        tmp_path,
        "balanced.yaml",
        profile_id="balanced",
        profile_extra="  probe_max_batch_size: 4\n  recommended: true",
    )
    _write_config(tmp_path, "experimental.yaml", profile_id="experimental", profile_extra="  probe_max_batch_size: 4")

    def fake_worker(root, config, batch_size, device, warmup_steps, measure_steps):
        return {
            "status": "pass",
            "batch_size": batch_size,
            "peak_reserved_fraction": 0.5,
            "peak_reserved_gib": 1.0,
            "headroom_gib": 3.5,
            "tokens_per_second": 100.0,
        }

    monkeypatch.setattr("godot_coder.profile_probe._run_worker", fake_worker)
    report = run_probe(
        tmp_path,
        ["configs/starter.yaml", "configs/balanced.yaml", "configs/experimental.yaml"],
        device="cpu",
        warmup_steps=0,
        measure_steps=1,
        max_batch_override=None,
    )
    assert report["format"] == "godot-coder-vram-profile-probe"
    assert len(report["profiles"]) == 3
    # The designed profile (balanced) is preferred when comfortable.
    assert report["recommendation"]["profile_id"] == "balanced"
    assert "headroom" in report["recommendation"]["reason"]
    latest = tmp_path / "reports" / "hardware" / "vram_probe_latest.json"
    assert latest.exists()
    assert json.loads(latest.read_text(encoding="utf-8"))["recommendation"]["profile_id"] == "balanced"
    stamped = sorted((tmp_path / "reports" / "hardware").glob("vram_probe_*.json"))
    assert len(stamped) == 2  # stamped + latest


def test_run_probe_oom_breaks_attempts(tmp_path, monkeypatch):
    _scaffold(tmp_path)
    _write_config(tmp_path, profile_extra="  probe_max_batch_size: 8")

    def fake_worker(root, config, batch_size, device, warmup_steps, measure_steps):
        if batch_size >= 4:
            return {"status": "oom", "batch_size": batch_size, "error": "CUDA out of memory"}
        return {"status": "pass", "batch_size": batch_size, "peak_reserved_fraction": 0.5}

    monkeypatch.setattr("godot_coder.profile_probe._run_worker", fake_worker)
    report = run_probe(
        tmp_path,
        ["configs/night.yaml"],
        device="cpu",
        warmup_steps=0,
        measure_steps=1,
        max_batch_override=None,
    )
    profile = report["profiles"][0]
    assert profile["first_oom_micro_batch"] == 4
    assert profile["largest_passing_micro_batch"] == 2
    # Attempts stop at the first OOM.
    assert [int(a["batch_size"]) for a in profile["attempts"]] == [1, 2, 4]
    assert profile["configured_result"]["batch_size"] == 2


# --- main: worker mode -------------------------------------------------------


def test_main_worker_mode_prints_result(monkeypatch, tmp_path, capsys):
    _scaffold(tmp_path)
    _write_config(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["probe", "--root", str(tmp_path), "--worker", "--config", "configs/night.yaml", "--batch-size", "2"],
    )
    monkeypatch.setattr(
        "godot_coder.profile_probe._worker_result",
        lambda *a, **k: {"status": "pass", "config": "configs/night.yaml", "batch_size": 2},
    )
    main()
    out = capsys.readouterr().out
    assert _RESULT_PREFIX in out
    payload = json.loads(out.split(_RESULT_PREFIX, 1)[1])
    assert payload["status"] == "pass"
    assert payload["batch_size"] == 2


def test_main_worker_error_exits_1(monkeypatch, tmp_path, capsys):
    _scaffold(tmp_path)
    _write_config(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["probe", "--root", str(tmp_path), "--worker", "--config", "configs/night.yaml", "--batch-size", "2"],
    )

    def fail(*a, **k):
        raise RuntimeError("CUDA not available")

    monkeypatch.setattr("godot_coder.profile_probe._worker_result", fail)
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    payload = json.loads(out.split(_RESULT_PREFIX, 1)[1])
    assert payload["status"] == "error"
    assert "CUDA not available" in payload["error"]
