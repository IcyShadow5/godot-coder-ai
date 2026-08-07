from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from godot_coder.corpus import validate_and_finalize
from godot_coder.godot_cli import build_project_validation_command


def _workspace(tmp_path: Path, files: dict[str, str], *, config_version: int = 5, old_status: str = "failed") -> tuple[Path, Path]:
    root = tmp_path
    corpus = root / "data" / "corpus"
    source_id = "demo"
    project = corpus / "downloads" / source_id / "game"
    project.mkdir(parents=True)
    (project / "project.godot").write_text(
        f"config_version={config_version}\n[application]\nconfig/name=\"Demo\"\n",
        encoding="utf-8",
    )
    staged = corpus / "staged" / source_id
    staged.mkdir(parents=True)
    records = []
    for index, (name, code) in enumerate(files.items()):
        source_file = project / name
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text(code, encoding="utf-8")
        staged_name = f"r{index}.gd"
        (staged / staged_name).write_text(code, encoding="utf-8")
        records.append({
            "record_id": f"r{index}",
            "source_id": source_id,
            "source_title": "Demo",
            "group_id": "demo::game",
            "kind": "godot_projects",
            "original_path": f"game/{name}",
            "staged_path": f"{source_id}/{staged_name}",
            "split": "train" if index == 0 else "val",
            "content_sha256": f"sha-{index}",
            "bytes": len(code.encode("utf-8")),
            "license": "MIT",
            "attribution": "Test",
            "source_commit": "abc",
            "project_root": str(project),
            "validation_status": old_status,
            "validation_error": "old false failure",
        })
    manifest = {
        "format": "godot-coder-licensed-corpus",
        "format_version": 3,
        "records": records,
        "sources": [],
        "skipped": [],
    }
    (corpus / "corpus_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root, project


def _patch_godot(monkeypatch: pytest.MonkeyPatch, output: str, return_code: int = 0) -> list[list[str]]:
    commands: list[list[str]] = []
    monkeypatch.setattr("godot_coder.corpus._find_godot", lambda: "godot.exe")
    monkeypatch.setattr("godot_coder.corpus._godot_version", lambda executable: "4.7.test")

    def fake_run(command: list[str], **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, return_code, output, "")

    monkeypatch.setattr("godot_coder.corpus._run", fake_run)
    return commands


def test_project_validation_command_imports_whole_project_once() -> None:
    command = build_project_validation_command("godot.exe", Path("demo/project"))
    assert command == [
        "godot.exe", "--headless", "--xr-mode", "off", "--disable-crash-handler",
        "--path", str(Path("demo/project")), "--import",
    ]
    assert "--script" not in command
    assert "--check-only" not in command


def test_old_file_failures_are_revalidated_once_per_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = _workspace(tmp_path, {
        "a.gd": "extends Node\nfunc a() -> void:\n\tpass\n",
        "nested/b.gd": "extends Resource\nfunc b() -> int:\n\treturn 2\n",
    })
    commands = _patch_godot(monkeypatch, "Godot Engine v4.7\n[ DONE ]")
    report = validate_and_finalize(root, minimum_accepted=0)
    assert report["passed"] == 2
    assert report["failed"] == 0
    assert report["revalidated_legacy_records"] == 2
    assert report["source_results"][0]["source_id"] == "demo"
    assert report["source_results"][0]["prepared"] == 2
    assert len(commands) == 2
    assert commands[0][-1] == "--import"
    assert "--script" in commands[1]
    manifest = json.loads((root / "data/corpus/corpus_manifest.json").read_text(encoding="utf-8"))
    assert manifest["validation_engine"] == "project-aware-v2"
    assert {item["validation_classification"] for item in manifest["records"]} == {"project_passed"}


def test_only_explicitly_mapped_syntax_error_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = _workspace(tmp_path, {
        "good.gd": "extends Node\nfunc good() -> void:\n\tpass\n",
        "bad.gd": "extends Node\nvar broken =\n",
    })
    output = (
        "SCRIPT ERROR: Parse Error: Expected expression after '='.\n"
        "   at: GDScript::reload (res://bad.gd:2)\n"
    )
    _patch_godot(monkeypatch, output, return_code=1)
    report = validate_and_finalize(root, minimum_accepted=0)
    assert report["passed"] == 1
    assert report["failed"] == 1
    assert report["classifications"]["syntax_error"] == 1
    manifest = json.loads((root / "data/corpus/corpus_manifest.json").read_text(encoding="utf-8"))
    by_path = {item["original_path"]: item for item in manifest["records"]}
    assert by_path["game/bad.gd"]["validation_status"] == "failed"
    assert by_path["game/good.gd"]["validation_status"] == "passed"


def test_missing_dependency_is_context_warning_not_mass_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = _workspace(tmp_path, {
        "uses_inventory.gd": "extends Node\nvar inventory: Inventory\n",
        "other.gd": "extends Control\n",
    })
    output = (
        'SCRIPT ERROR: Parse Error: Could not find type "Inventory" in the current scope.\n'
        "   at: GDScript::reload (res://uses_inventory.gd:2)\n"
        "ERROR: Failed to load resource: res://missing_icon.png.\n"
    )
    _patch_godot(monkeypatch, output, return_code=1)
    report = validate_and_finalize(root, minimum_accepted=0)
    assert report["passed"] == 2
    assert report["failed"] == 0
    assert report["context_warnings"] == 2
    assert report["prepared"] == 2


def test_godot3_project_is_classified_separately_without_launching_godot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = _workspace(tmp_path, {"legacy.gd": "extends Node\n"}, config_version=4)
    commands = _patch_godot(monkeypatch, "")
    report = validate_and_finalize(root, minimum_accepted=0)
    assert report["failed"] == 1
    assert report["classifications"]["legacy_godot3"] == 1
    assert commands == []


def test_corpus_progress_event_is_structured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root, _ = _workspace(tmp_path, {"a.gd": "extends Node\n"})
    _patch_godot(monkeypatch, "Godot Engine v4.7")
    validate_and_finalize(root, minimum_accepted=0)
    output = capsys.readouterr().out
    assert "GCAI_EVENT " in output
    assert '"event": "corpus_validation_progress"' in output
    assert "validate=1/1" not in output
