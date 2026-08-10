"""Tests for the doctor CLI — environment/runtime diagnostics."""

import sys
from types import SimpleNamespace

import pytest
import torch

from godot_coder.doctor import _find_godot, collect_status, parse_args


def _complete_project(root) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (root / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (root / "configs").mkdir()
    (root / "configs" / "corpus_starter_30m.yaml").write_text("model:\n", encoding="utf-8")
    (root / "src" / "godot_coder").mkdir(parents=True)
    (root / "src" / "godot_coder" / "model.py").write_text("# model\n", encoding="utf-8")


def _cpu_only(monkeypatch, godot=None):
    monkeypatch.setattr("godot_coder.doctor.torch.cuda.is_available", lambda: False)
    monkeypatch.setattr(torch.version, "cuda", None)
    if godot is None:
        monkeypatch.setattr("godot_coder.doctor._find_godot", lambda: None)
    else:
        monkeypatch.setattr("godot_coder.doctor._find_godot", lambda: godot)
        monkeypatch.setattr(
            "godot_coder.doctor.subprocess.run",
            lambda *a, **k: SimpleNamespace(stdout="4.2.1\n", stderr="", returncode=0),
        )


def test_parse_args_json_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["doctor", "--json"])
    args = parse_args()
    assert args.json is True


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
    status, _ = collect_status(tmp_path)
    # A failed version probe is reported as a problem but is not a hard
    # failure on its own — the executable was still found.
    assert any("version command failed" in p for p in status["problems"])
    assert status["godot"]["returncode"] == 1


def test_godot_not_in_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert _find_godot() is None


def test_godot_found_in_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "C:/godot/godot.exe" if "godot" in name else None)
    assert _find_godot() == "C:/godot/godot.exe"


def test_collect_status_has_core_keys(monkeypatch, tmp_path):
    _complete_project(tmp_path)
    _cpu_only(monkeypatch, godot="C:/godot/godot.exe")
    status, _ = collect_status(tmp_path)
    for key in ("app_version", "project_root", "python", "pytorch", "cuda_available", "ok", "problems"):
        assert key in status
