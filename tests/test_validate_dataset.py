"""Tests for the validate_dataset CLI — per-file Godot parser checks."""

import json
from types import SimpleNamespace

import pytest

from godot_coder.validate_dataset import (
    PerFileResult,
    ValidationReport,
    find_godot,
    validate_dataset,
)


def _minimal_project(root) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.godot").write_text('config_version=5\n[application]\nconfig/name="Test"\n', encoding="utf-8")
    (root / "a.gd").write_text("extends Node\n", encoding="utf-8")
    (root / "b.gd").write_text("extends Node2D\n", encoding="utf-8")


def _fake_run(*, return_code=0, timed_out=False, startup_error=None, output=""):
    return SimpleNamespace(
        output=output,
        startup_error=startup_error,
        timed_out=timed_out,
        return_code=return_code,
    )


def test_find_godot_found(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "C:/godot/godot.exe" if "godot" in name else None)
    assert find_godot() is not None


def test_find_godot_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert find_godot() is None


def test_missing_project_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="project.godot"):
        validate_dataset(tmp_path, godot="godot")


def test_missing_godot_raises(tmp_path, monkeypatch):
    root = tmp_path / "p"
    _minimal_project(root)
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(FileNotFoundError, match="Godot executable"):
        validate_dataset(root, godot=None)


def test_validate_all_pass(monkeypatch, tmp_path):
    root = tmp_path / "p"
    _minimal_project(root)
    monkeypatch.setattr(
        "godot_coder.validate_dataset.run_managed_process",
        lambda *args, **kwargs: _fake_run(return_code=0),
    )
    report = validate_dataset(root, godot="godot")
    assert report.total == 2
    assert report.passed == 2
    assert report.failed == 0
    assert report.pass_rate == 1.0
    assert (root / "validation_report.json").exists()
    saved = json.loads((root / "validation_report.json").read_text(encoding="utf-8"))
    assert saved["format"] == "godot-coder-dataset-validation"
    assert saved["passed"] == 2


def test_validate_records_failures(monkeypatch, tmp_path):
    root = tmp_path / "p"
    _minimal_project(root)
    results = {"a.gd": 0, "b.gd": 1}

    def _by_script(*args, **kwargs):
        from pathlib import Path as _Path

        command = args[0]
        script = _Path(command[command.index("--script") + 1]).name
        return _fake_run(return_code=results.get(script, 0))

    monkeypatch.setattr("godot_coder.validate_dataset.run_managed_process", _by_script)
    report = validate_dataset(root, godot="godot")
    assert report.passed == 1
    assert report.failed == 1
    assert report.pass_rate == 0.5


def test_validate_timeout_marks_failed(monkeypatch, tmp_path):
    root = tmp_path / "p"
    _minimal_project(root)
    monkeypatch.setattr(
        "godot_coder.validate_dataset.run_managed_process",
        lambda *args, **kwargs: _fake_run(timed_out=True),
    )
    with pytest.warns(UserWarning, match="timed out"):
        report = validate_dataset(root, godot="godot")
    assert report.failed == 2
    assert all(r.return_code == -1 for r in report.results)


def test_validate_startup_error_marks_failed(monkeypatch, tmp_path):
    root = tmp_path / "p"
    _minimal_project(root)
    monkeypatch.setattr(
        "godot_coder.validate_dataset.run_managed_process",
        lambda *args, **kwargs: _fake_run(startup_error="boom"),
    )
    with pytest.warns(UserWarning, match="failed to start"):
        report = validate_dataset(root, godot="godot")
    assert report.failed == 2


def test_validate_crash_marks_failed(monkeypatch, tmp_path):
    root = tmp_path / "p"
    _minimal_project(root)

    def _crash(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("godot_coder.validate_dataset.run_managed_process", _crash)
    with pytest.warns(UserWarning, match="crashed"):
        report = validate_dataset(root, godot="godot")
    assert report.failed == 2
    assert report.results[0].output == "RuntimeError: kaboom"


def test_custom_timeout_forwarded(monkeypatch, tmp_path):
    root = tmp_path / "p"
    _minimal_project(root)
    seen = {}

    def _capture(*args, **kwargs):
        seen.update(kwargs)
        return _fake_run(return_code=0)

    monkeypatch.setattr("godot_coder.validate_dataset.run_managed_process", _capture)
    validate_dataset(root, godot="godot", timeout=3.5)
    assert seen["timeout_seconds"] == 3.5


def test_per_file_result_fields():
    result = PerFileResult(path="a.gd", passed=True, return_code=0, output="ok")
    assert result.path == "a.gd"
    assert result.passed is True
    assert result.return_code == 0
    assert result.output == "ok"


def test_validation_report_to_dict():
    report = ValidationReport(
        input="/x",
        total=1,
        passed=1,
        failed=0,
        pass_rate=1.0,
        results=[PerFileResult(path="a.gd", passed=True, return_code=0, output="ok")],
    )
    data = report.to_dict()
    assert data["format"] == "godot-coder-dataset-validation"
    assert data["results"][0]["path"] == "a.gd"
