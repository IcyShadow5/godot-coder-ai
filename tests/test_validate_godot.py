"""Tests for the validate_godot CLI runner (standalone GDScript parser check)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from godot_coder.process_control import ManagedProcessResult
from godot_coder.validate_godot import main as validate_godot_main


def _seed_project(tmp_path: Path) -> Path:
    """Create a minimal project.godot so the validator doesn't reject the root."""
    project = tmp_path / "seed"
    project.mkdir()
    (project / "project.godot").write_text(
        "[application]\nconfig/name=\"test\"\n", encoding="utf-8"
    )
    (project / "test.gd").write_text("extends Node\n", encoding="utf-8")
    return project


def _run_with_args(args: SimpleNamespace, monkeypatch) -> None:
    """Call main() with parse_args stubbed out. pytest's monkeypatch
    restores the original automatically when the test finishes."""
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self, *a, **kw: args)
    validate_godot_main()


def test_validate_godot_exits_clean_when_godot_not_found(
    tmp_path: Path, monkeypatch
) -> None:
    """validate_godot should raise FileNotFoundError when Godot is missing."""
    project = _seed_project(tmp_path)
    args = SimpleNamespace(
        script=str(project / "test.gd"),
        project=str(project),
        godot="nonexistent-godot-binary-xyz",
        timeout=30,
    )
    with pytest.raises(FileNotFoundError, match="Godot executable not found"):
        _run_with_args(args, monkeypatch)


def test_validate_godot_exits_clean_when_project_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """validate_godot should raise FileNotFoundError when project.godot is missing."""
    missing = tmp_path / "no-project"
    missing.mkdir()
    (missing / "test.gd").write_text("extends Node\n", encoding="utf-8")
    args = SimpleNamespace(
        script=str(missing / "test.gd"),
        project=str(missing),
        godot="godot",
        timeout=30,
    )
    with pytest.raises(FileNotFoundError, match="project.godot not found"):
        _run_with_args(args, monkeypatch)


def test_validate_godot_exits_clean_when_script_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """validate_godot should raise FileNotFoundError when the script file is missing."""
    project = _seed_project(tmp_path)
    args = SimpleNamespace(
        script=str(project / "nonexistent.gd"),
        project=str(project),
        godot="godot",
        timeout=30,
    )
    with pytest.raises(FileNotFoundError):
        _run_with_args(args, monkeypatch)


def test_validate_godot_uses_run_managed_process(
    tmp_path: Path, monkeypatch
) -> None:
    """validate_godot must use run_managed_process, not raw subprocess.run."""
    project = _seed_project(tmp_path)
    calls: list[dict] = []

    def fake_managed(command, **kwargs):
        calls.append({"command": command, "kwargs": kwargs})
        return ManagedProcessResult(
            command=list(command),
            return_code=0,
            output="parse ok",
            timed_out=False,
            duration_seconds=0.1,
            pid=9999,
            termination_attempted=False,
        )

    monkeypatch.setattr(
        "godot_coder.validate_godot.run_managed_process", fake_managed
    )

    args = SimpleNamespace(
        script=str(project / "test.gd"),
        project=str(project),
        godot=sys.executable,
        timeout=30,
    )
    _run_with_args(args, monkeypatch)
    assert len(calls) == 1, "run_managed_process must be called exactly once"
    assert "timeout_seconds" in calls[0]["kwargs"]
    assert "idle_timeout_seconds" in calls[0]["kwargs"]


def test_validate_godot_reports_timeout(
    tmp_path: Path, monkeypatch
) -> None:
    """A timed-out Godot parse should cause SystemExit(1)."""
    project = _seed_project(tmp_path)

    def fake_managed(command, **kwargs):
        return ManagedProcessResult(
            command=list(command),
            return_code=None,
            output="still busy",
            timed_out=True,
            duration_seconds=30.0,
            pid=9999,
            termination_attempted=True,
        )

    monkeypatch.setattr(
        "godot_coder.validate_godot.run_managed_process", fake_managed
    )

    args = SimpleNamespace(
        script=str(project / "test.gd"),
        project=str(project),
        godot=sys.executable,
        timeout=30,
    )
    with pytest.raises(SystemExit) as exc_info:
        _run_with_args(args, monkeypatch)
    assert exc_info.value.code == 1


def test_validate_godot_reports_startup_error(
    tmp_path: Path, monkeypatch
) -> None:
    """A startup error (Godot crash on launch) should cause SystemExit(1)."""
    project = _seed_project(tmp_path)

    def fake_managed(command, **kwargs):
        return ManagedProcessResult(
            command=list(command),
            return_code=None,
            output="",
            timed_out=False,
            duration_seconds=0.05,
            pid=None,
            termination_attempted=False,
            startup_error="FileNotFoundError: godot not found",
        )

    monkeypatch.setattr(
        "godot_coder.validate_godot.run_managed_process", fake_managed
    )

    args = SimpleNamespace(
        script=str(project / "test.gd"),
        project=str(project),
        godot=sys.executable,
        timeout=30,
    )
    with pytest.raises(SystemExit) as exc_info:
        _run_with_args(args, monkeypatch)
    assert exc_info.value.code == 1
