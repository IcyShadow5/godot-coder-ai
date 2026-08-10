"""Tests for the doctor CLI — environment/runtime diagnostics.

Covers the CPU-only and GPU paths through ``collect_status``, the Godot
probe (found / missing / failing subprocess), project-file checks, and the
``main()`` entry point incl. JSON output and exit codes. All hardware calls
are mocked, so the suite runs deterministically without CUDA or Godot.
"""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
import torch

from godot_coder.doctor import _find_godot, collect_status, main, parse_args


def _complete_project(root) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("[project]", encoding="utf-8")
    (root / "LICENSE").write_text("MIT", encoding="utf-8")
    (root / "configs").mkdir()
    (root / "configs" / "corpus_starter_30m.yaml").write_text("model:", encoding="utf-8")
    (root / "src" / "godot_coder").mkdir(parents=True)
    (root / "src" / "godot_coder" / "model.py").write_text("# model", encoding="utf-8")


def _cpu_only(monkeypatch, godot=None):
    monkeypatch.setattr("godot_coder.doctor.torch.cuda.is_available", lambda: False)
    monkeypatch.setattr(torch.version, "cuda", None)
    if godot is None:
        monkeypatch.setattr("godot_coder.doctor._find_godot", lambda: None)
    else:
        monkeypatch.setattr("godot_coder.doctor._find_godot", lambda: godot)
        monkeypatch.setattr(
            "godot_coder.doctor.subprocess.run",
            lambda *a, **k: SimpleNamespace(stdout="4.2.1", stderr="", returncode=0),
        )


def _cuda_available(monkeypatch, godot="C:/godot/godot.exe", *, attention=None, randn_error=None):
    # doctor imports torch directly, so the patch lands on the shared torch
    # module - freeze the real function first or the mock calls itself.
    real_randn = torch.randn
    monkeypatch.setattr("godot_coder.doctor.torch.cuda.is_available", lambda: True)
    monkeypatch.setattr(
        "godot_coder.doctor.torch.cuda.get_device_properties",
        lambda device: SimpleNamespace(name="Test GPU", major=8, minor=6, total_memory=6 * 1024**3),
    )
    monkeypatch.setattr("godot_coder.doctor.torch.cuda.synchronize", lambda device: None)
    if randn_error is not None:
        def boom(*a, **k):
            raise RuntimeError(randn_error)

        monkeypatch.setattr("godot_coder.doctor.torch.randn", boom)
    else:
        monkeypatch.setattr("godot_coder.doctor.torch.randn", lambda *a, **k: real_randn(1, 2, 16, 32))
    if attention is not None:
        monkeypatch.setattr("godot_coder.doctor.F.scaled_dot_product_attention", lambda *a, **k: attention)
    else:
        monkeypatch.setattr(
            "godot_coder.doctor.F.scaled_dot_product_attention",
            lambda *a, **k: real_randn(1, 2, 16, 32),
        )
    if godot is None:
        monkeypatch.setattr("godot_coder.doctor._find_godot", lambda: None)
    else:
        monkeypatch.setattr("godot_coder.doctor._find_godot", lambda: godot)
        monkeypatch.setattr(
            "godot_coder.doctor.subprocess.run",
            lambda *a, **k: SimpleNamespace(stdout="4.2.1", stderr="", returncode=0),
        )


# --- argument parsing --------------------------------------------------------


def test_parse_args_json_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["doctor", "--json"])
    args = parse_args()
    assert args.json is True


# --- collect_status: CPU-only paths ------------------------------------------


def test_collect_status_cpu_only_without_godot(monkeypatch, tmp_path):
    _complete_project(tmp_path)
    _cpu_only(monkeypatch)
    status, ok = collect_status(tmp_path)
    assert ok is False
    assert status["cuda_available"] is False
    assert any("CPU-only" in p for p in status["problems"])
    assert any("Godot was not found" in p for p in status["problems"])
    assert status["project_files"]["passed"] is True


def test_collect_status_cpu_only_with_godot(monkeypatch, tmp_path):
    _complete_project(tmp_path)
    _cpu_only(monkeypatch, godot="C:/godot/godot.exe")
    status, ok = collect_status(tmp_path)
    assert ok is True
    assert status["godot"]["version"] == "4.2.1"
    assert status["godot"]["returncode"] == 0


def test_collect_status_missing_project_files(monkeypatch, tmp_path):
    root = tmp_path / "empty"
    root.mkdir(parents=True)
    _cpu_only(monkeypatch, godot="C:/godot/godot.exe")
    status, ok = collect_status(root)
    assert ok is False
    assert not status["project_files"]["passed"]
    assert "pyproject.toml" in status["project_files"]["missing"]
    assert any("Missing project files" in p for p in status["problems"])


def test_collect_status_godot_version_fails_reports_problem(monkeypatch, tmp_path):
    _complete_project(tmp_path)
    _cpu_only(monkeypatch, godot="C:/godot/godot.exe")
    monkeypatch.setattr(
        "godot_coder.doctor.subprocess.run",
        lambda *a, **k: SimpleNamespace(stdout="", stderr="", returncode=1),
    )
    status, ok = collect_status(tmp_path)
    # A failed version probe is reported as a problem but is not a hard
    # failure on its own — the executable was still found. The parametrized
    # probe-failure test below pins the opposite (hard) case.
    assert any("version command failed" in p for p in status["problems"])
    assert status["godot"]["returncode"] == 1
    assert ok is True


# --- collect_status: CUDA paths ----------------------------------------------


def test_collect_status_cuda_available_passes(monkeypatch, tmp_path):
    _complete_project(tmp_path)
    _cuda_available(monkeypatch)
    status, ok = collect_status(tmp_path)
    assert ok is True
    assert status["cuda_available"] is True
    assert status["cuda_test"] == "passed"
    assert status["gpu"] == {
        "name": "Test GPU",
        "compute_capability": "8.6",
        "vram_gib": 6.0,
    }
    assert not any("CUDA" in p for p in status["problems"])


def test_collect_status_cuda_attention_non_finite_fails(monkeypatch, tmp_path):
    _complete_project(tmp_path)
    nan = torch.full((1, 2, 16, 32), float("nan"))
    _cuda_available(monkeypatch, attention=nan)
    status, ok = collect_status(tmp_path)
    assert status["cuda_test"] == "failed"
    assert any("non-finite" in p for p in status["problems"])
    assert ok is False


def test_collect_status_cuda_runtime_exception(monkeypatch, tmp_path):
    _complete_project(tmp_path)
    _cuda_available(monkeypatch, randn_error="out of memory")
    status, ok = collect_status(tmp_path)
    assert status["cuda_test"] == "failed"
    assert any("CUDA runtime test failed: RuntimeError: out of memory" in p for p in status["problems"])
    assert ok is False


def test_collect_status_cuda_build_but_unavailable(monkeypatch, tmp_path):
    _complete_project(tmp_path)
    _cpu_only(monkeypatch, godot="C:/godot/godot.exe")
    monkeypatch.setattr(torch.version, "cuda", "12.1")
    status, ok = collect_status(tmp_path)
    assert any("CUDA build, but CUDA is not available" in p for p in status["problems"])
    # Like CPU-only, a CUDA-less machine is still usable for tests.
    assert ok is True


# --- collect_status: Godot probe failures ------------------------------------


@pytest.mark.parametrize(
    "exc",
    [subprocess.TimeoutExpired(["godot"], 15), OSError("no such file")],
    ids=["timeout", "oserror"],
)
def test_collect_status_godot_probe_failure_is_hard(monkeypatch, tmp_path, exc):
    _complete_project(tmp_path)
    monkeypatch.setattr("godot_coder.doctor.torch.cuda.is_available", lambda: False)
    monkeypatch.setattr(torch.version, "cuda", None)
    monkeypatch.setattr("godot_coder.doctor._find_godot", lambda: "C:/godot/godot.exe")

    def fail(*a, **k):
        raise exc

    monkeypatch.setattr("godot_coder.doctor.subprocess.run", fail)
    status, ok = collect_status(tmp_path)
    assert any("Godot check failed" in p for p in status["problems"])
    # Unlike a non-zero version probe, a crashed probe leaves no godot entry,
    # which is a hard failure.
    assert status["godot"] is None
    assert ok is False


# --- _find_godot --------------------------------------------------------------


def test_godot_not_in_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert _find_godot() is None


def test_godot_found_in_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "C:/godot/godot.exe" if "godot" in name else None)
    assert _find_godot() == "C:/godot/godot.exe"


# --- collect_status: structure -------------------------------------------------


def test_collect_status_has_core_keys(monkeypatch, tmp_path):
    _complete_project(tmp_path)
    _cpu_only(monkeypatch, godot="C:/godot/godot.exe")
    status, _ = collect_status(tmp_path)
    for key in ("app_version", "project_root", "python", "pytorch", "cuda_available", "ok", "problems"):
        assert key in status


# --- main(): JSON output and exit codes ---------------------------------------


def test_main_json_exit_ok(monkeypatch, tmp_path, capsys):
    _complete_project(tmp_path)
    _cpu_only(monkeypatch, godot="C:/godot/godot.exe")
    monkeypatch.setattr(sys, "argv", ["doctor", "--json", "--root", str(tmp_path)])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert "app_version" in payload


def test_main_json_exit_fail(monkeypatch, tmp_path, capsys):
    _complete_project(tmp_path)
    _cpu_only(monkeypatch)  # no godot found -> not ok
    monkeypatch.setattr(sys, "argv", ["doctor", "--json", "--root", str(tmp_path)])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_main_human_output(monkeypatch, tmp_path, capsys):
    _complete_project(tmp_path)
    _cpu_only(monkeypatch, godot="C:/godot/godot.exe")
    monkeypatch.setattr(sys, "argv", ["doctor", "--root", str(tmp_path)])
    with pytest.raises(SystemExit):
        main()
    out = capsys.readouterr().out
    assert "Godot Coder AI:" in out
    assert "PyTorch:" in out
    assert "Godot: 4.2.1" in out
