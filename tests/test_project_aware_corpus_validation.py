from __future__ import annotations

import json
from pathlib import Path

import pytest

from godot_coder.corpus import validate_and_finalize
from godot_coder.godot_cli import build_project_validation_command
from godot_coder.process_control import ManagedProcessResult


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

    def fake_managed(command: list[str], **kwargs):
        commands.append(command)
        return ManagedProcessResult(
            command=command,
            return_code=return_code,
            output=output,
            timed_out=False,
            duration_seconds=0.1,
            pid=None,
            termination_attempted=False,
        )

    monkeypatch.setattr("godot_coder.corpus.run_managed_process", fake_managed)
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
    assert manifest["validation_engine"] == "project-aware-v4"
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

def test_validate_runs_godot_through_job_object_managed_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: validation previously used raw subprocess.run with capture_output,
    # which only kills the direct child on timeout. A Mono Godot build spawns a
    # grandchild that inherits the stdout pipe handles, so communicate() then
    # blocks forever. The managed runner (Windows job objects) kills the whole
    # tree - assert the validate path goes through it with sane timeouts.
    root, _ = _workspace(tmp_path, {"a.gd": "extends Node\n"})
    captured: list[dict[str, object]] = []
    monkeypatch.setattr("godot_coder.corpus._find_godot", lambda: "godot.exe")
    monkeypatch.setattr("godot_coder.corpus._godot_version", lambda executable: "4.7.test")

    def fake_managed(command: list[str], **kwargs):
        captured.append(kwargs)
        return ManagedProcessResult(
            command=command, return_code=0, output="Godot Engine v4.7\n[ DONE ]",
            timed_out=False, duration_seconds=0.1, pid=None, termination_attempted=False,
        )

    monkeypatch.setattr("godot_coder.corpus.run_managed_process", fake_managed)
    validate_and_finalize(root, minimum_accepted=0)
    assert len(captured) == 2  # project import + script checker
    assert captured[0]["timeout_seconds"] == 120  # default from GODOT_CODER_VALIDATION_TIMEOUT_SECONDS
    assert captured[1]["timeout_seconds"] == 120  # checker floor of 120s
    assert captured[0]["idle_timeout_seconds"] == 30.0  # default from GODOT_CODER_VALIDATION_IDLE_TIMEOUT_SECONDS


def test_managed_timeout_keeps_records_with_context_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: a hung Godot caught by the managed runner must not take the
    # whole validate job down. Records survive with a context warning instead of
    # being rejected, and the job returns a normal report.
    root, _ = _workspace(tmp_path, {"a.gd": "extends Node\nfunc a() -> void:\n\tpass\n"})
    monkeypatch.setattr("godot_coder.corpus._find_godot", lambda: "godot.exe")
    monkeypatch.setattr("godot_coder.corpus._godot_version", lambda executable: "4.7.test")

    def fake_managed(command: list[str], **kwargs):
        return ManagedProcessResult(
            command=command, return_code=None, output="", timed_out=True,
            duration_seconds=61.0, pid=None, termination_attempted=True, idle_timed_out=True,
        )

    monkeypatch.setattr("godot_coder.corpus.run_managed_process", fake_managed)
    report = validate_and_finalize(root, minimum_accepted=0)
    assert report["failed"] == 0
    assert report["passed"] == 1
    assert report["context_warnings"] == 1
def test_validation_timeout_env_var_is_honored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The README documents GODOT_CODER_VALIDATION_TIMEOUT_SECONDS / IDLE as
    # configurable; corpus.py must honor them instead of hardcoding.
    monkeypatch.setenv("GODOT_CODER_VALIDATION_TIMEOUT_SECONDS", "240")
    monkeypatch.setenv("GODOT_CODER_VALIDATION_IDLE_TIMEOUT_SECONDS", "45")
    root, _ = _workspace(tmp_path, {"a.gd": "extends Node\n"})
    captured: list[dict[str, object]] = []
    monkeypatch.setattr("godot_coder.corpus._find_godot", lambda: "godot.exe")
    monkeypatch.setattr("godot_coder.corpus._godot_version", lambda executable: "4.7.test")

    def fake_managed(command: list[str], **kwargs):
        captured.append(kwargs)
        return ManagedProcessResult(
            command=command, return_code=0, output="Godot Engine v4.7\n[ DONE ]",
            timed_out=False, duration_seconds=0.1, pid=None, termination_attempted=False,
        )

    monkeypatch.setattr("godot_coder.corpus.run_managed_process", fake_managed)
    validate_and_finalize(root, minimum_accepted=0)
    assert captured[0]["timeout_seconds"] == 240
    assert captured[1]["timeout_seconds"] == 240  # checker floor is 120 but env wins above it
    assert captured[0]["idle_timeout_seconds"] == 45


def test_project_less_record_is_validated_per_file_and_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: pure addon repos (no project.godot) were hard-excluded as
    # "missing_project". Policy: only clear syntax errors + Godot-3 are hard
    # excludes - project-less scripts get a standalone --check-only parse and
    # are kept with a context warning at worst.
    root = tmp_path
    corpus = root / "data" / "corpus"
    source_root = corpus / "downloads" / "demo-addon"
    (source_root / "addons" / "demo").mkdir(parents=True, exist_ok=True)
    (source_root / "addons" / "demo" / "util.gd").write_text(
        "extends Node\nfunc util() -> int:\n\treturn 1\n", encoding="utf-8"
    )
    staged = corpus / "staged" / "demo-addon"
    staged.mkdir(parents=True)
    (staged / "r0.gd").write_text("extends Node\n", encoding="utf-8")
    records = [{
        "record_id": "r0", "source_id": "demo-addon", "source_title": "Demo Addon",
        "group_id": "demo-addon::addons/demo", "kind": "godot_projects",
        "original_path": "addons/demo/util.gd", "staged_path": "demo-addon/r0.gd",
        "split": "train", "content_sha256": "sha-0", "bytes": 40, "license": "MIT",
        "attribution": "Test", "source_commit": "abc", "project_root": None,
        "validation_status": "pending", "validation_error": None,
    }]
    manifest = {
        "format": "godot-coder-licensed-corpus", "format_version": 3,
        "records": records, "sources": [], "skipped": [],
    }
    (corpus / "corpus_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr("godot_coder.corpus._find_godot", lambda: "godot.exe")
    monkeypatch.setattr("godot_coder.corpus._godot_version", lambda executable: "4.7.test")

    def fake_managed(command: list[str], **kwargs):
        # Standalone --check-only on a valid script: no errors, exit 0.
        return ManagedProcessResult(
            command=command, return_code=0, output="Godot Engine v4.7",
            timed_out=False, duration_seconds=0.1, pid=None, termination_attempted=False,
        )

    monkeypatch.setattr("godot_coder.corpus.run_managed_process", fake_managed)
    report = validate_and_finalize(root, minimum_accepted=0)
    assert report["failed"] == 0
    assert report["passed"] == 1
    assert report["prepared"] == 1
    manifest_now = json.loads((corpus / "corpus_manifest.json").read_text(encoding="utf-8"))
    assert manifest_now["records"][0]["validation_classification"] == "context_warning"


def test_project_less_record_with_clear_syntax_error_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A project-less addon script with a real parse error must still be hard
    # excluded - the standalone path classifies hard syntax errors.
    root = tmp_path
    corpus = root / "data" / "corpus"
    source_root = corpus / "downloads" / "demo-addon"
    source_root.mkdir(parents=True)
    (source_root / "broken.gd").write_text("extends Node\nvar broken =\n", encoding="utf-8")
    staged = corpus / "staged" / "demo-addon"
    staged.mkdir(parents=True)
    (staged / "r0.gd").write_text("extends Node\nvar broken =\n", encoding="utf-8")
    records = [{
        "record_id": "r0", "source_id": "demo-addon", "source_title": "Demo Addon",
        "group_id": "demo-addon::root", "kind": "godot_projects",
        "original_path": "broken.gd", "staged_path": "demo-addon/r0.gd",
        "split": "train", "content_sha256": "sha-0", "bytes": 40, "license": "MIT",
        "attribution": "Test", "source_commit": "abc", "project_root": None,
        "validation_status": "pending", "validation_error": None,
    }]
    manifest = {
        "format": "godot-coder-licensed-corpus", "format_version": 3,
        "records": records, "sources": [], "skipped": [],
    }
    (corpus / "corpus_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr("godot_coder.corpus._find_godot", lambda: "godot.exe")
    monkeypatch.setattr("godot_coder.corpus._godot_version", lambda executable: "4.7.test")

    def fake_managed(command: list[str], **kwargs):
        output = (
            "SCRIPT ERROR: Parse Error: Expected expression after '='.\n"
            "   at: GDScript::reload (res://broken.gd:2)\n"
        )
        return ManagedProcessResult(
            command=command, return_code=1, output=output,
            timed_out=False, duration_seconds=0.1, pid=None, termination_attempted=False,
        )

    monkeypatch.setattr("godot_coder.corpus.run_managed_process", fake_managed)
    report = validate_and_finalize(root, minimum_accepted=0)
    assert report["failed"] == 1
    assert report["classifications"]["syntax_error"] == 1
    assert report["prepared"] == 0

def test_invalid_statement_parse_error_is_hard_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: "Invalid statement."-style parse errors are unambiguous
    # syntax errors but were not in the hard marker list, so they slipped
    # through as context warnings and were included in the dataset.
    root, _ = _workspace(tmp_path, {
        "good.gd": "extends Node\nfunc good() -> void:\n\tpass\n",
        "bad.gd": "extends Node\nvar broken =\n",
    })
    output = (
        "SCRIPT ERROR: Parse Error: Invalid statement.\n"
        "   at: GDScript::reload (res://bad.gd:2)\n"
    )
    _patch_godot(monkeypatch, output, return_code=1)
    report = validate_and_finalize(root, minimum_accepted=0)
    assert report["failed"] == 1
    assert report["classifications"]["syntax_error"] == 1
    assert report["passed"] == 1
    assert report["prepared"] == 1


def test_new_hard_markers_all_reject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for index, message in enumerate((
        "Invalid statement.",
        "Invalid use of '='.",
        "Invalid assignment.",
        "Expected identifier.",
        "Constant expected.",
    )):
        root, _ = _workspace(tmp_path / f"case-{index}", {"bad.gd": "extends Node\nvar broken =\n"})
        output = f"SCRIPT ERROR: Parse Error: {message}\n   at: GDScript::reload (res://bad.gd:2)\n"
        _patch_godot(monkeypatch, output, return_code=1)
        report = validate_and_finalize(root, minimum_accepted=0)
        assert report["classifications"]["syntax_error"] == 1, message
        assert report["failed"] == 1, message


def test_adjacent_context_error_does_not_demote_syntax_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: classification used the +/-3 line block around an error. A
    # benign context error printed directly next to a real parse error demoted
    # it to a warning, so the broken file was included. Only the error line may
    # decide hard vs context; the block is only for path attribution.
    root, _ = _workspace(tmp_path, {
        "good.gd": "extends Node\nfunc good() -> void:\n\tpass\n",
        "bad.gd": "extends Node\nvar broken =\n",
    })
    output = (
        "SCRIPT ERROR: Parse Error: Expected expression after '='.\n"
        "   at: GDScript::reload (res://bad.gd:2)\n"
        "ERROR: Failed to load resource: res://missing_icon.png.\n"
        "   at: resource_loader.cpp:1234\n"
    )
    _patch_godot(monkeypatch, output, return_code=1)
    report = validate_and_finalize(root, minimum_accepted=0)
    assert report["failed"] == 1
    assert report["classifications"]["syntax_error"] == 1
    assert report["passed"] == 1


def test_validation_cache_is_isolated_per_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The v0.10.4/v0.10.5 classification changes must not replay older
    # decisions: a fresh cache file per version forces a clean re-check.
    from godot_coder.corpus import _validation_cache_path
    root, _ = _workspace(tmp_path, {"a.gd": "extends Node\n"})
    path = _validation_cache_path(root)
    assert path.name == "godot_project_validation_v4.json"
    _patch_godot(monkeypatch, "Godot Engine v4.7")
    validate_and_finalize(root, minimum_accepted=0)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["format_version"] == 4
    assert payload["validator"] == "project-aware-v4"


def test_checker_script_ignores_warning_as_error_projects(tmp_path: Path) -> None:
    # gdUnit4 (and other strict projects) promote GDScript warnings to errors.
    # The generated per-file checker failed to compile under those settings
    # ("for iterator variable has no static type"), so no file in the project
    # got an individual marker and all of them were kept unverified. The
    # checker must carry warning-ignore regions and explicit types.
    from godot_coder.corpus import _write_project_checker
    root = tmp_path
    helper = _write_project_checker(root, "abc123", ["a.gd", "b.gd"])
    text = helper.read_text(encoding="utf-8")
    assert "@warning_ignore_start" in text
    assert "@warning_ignore_start" in text
    assert "path_value: String in paths" in text
    assert "Array[String]" in text
    assert "path_value: String in paths" in text
    assert "Array[String]" in text

def test_context_warning_record_is_perfile_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A record that ends up as a context warning (the checker produced no
    # marker for it) must still get a standalone --check-only parse. A clean
    # parse keeps the context warning but records that the file was verified.
    root, _ = _workspace(tmp_path, {"a.gd": "extends Node\nfunc a() -> void:\n\tpass\n"})
    calls: list[list[str]] = []
    monkeypatch.setattr("godot_coder.corpus._find_godot", lambda: "godot.exe")
    monkeypatch.setattr("godot_coder.corpus._godot_version", lambda executable: "4.7.test")

    def fake_managed(command: list[str], **kwargs):
        calls.append(command)
        if "--check-only" in command:
            output, rc = "Godot Engine v4.7\n", 0
        else:
            output, rc = "Godot Engine v4.7\nERROR: Failed to load resource: res://missing.png.\n", 1
        return ManagedProcessResult(
            command=command, return_code=rc, output=output,
            timed_out=False, duration_seconds=0.1, pid=None, termination_attempted=False,
        )

    monkeypatch.setattr("godot_coder.corpus.run_managed_process", fake_managed)
    report = validate_and_finalize(root, minimum_accepted=0)
    assert report["failed"] == 0
    assert report["context_warnings"] == 1
    assert report["prepared"] == 1
    perfile_calls = [c for c in calls if "--check-only" in c]
    assert len(perfile_calls) == 1


def test_context_warning_with_real_syntax_error_is_now_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The case v0.10.5 exists for: inside the project the file merely "fails
    # to load" (no hard error attributed, so it would be kept as an unverified
    # context warning), but the standalone parse reveals a real syntax error.
    # It must now be a hard exclusion instead of slipping into the dataset.
    root, _ = _workspace(tmp_path, {
        "good.gd": "extends Node\nfunc good() -> void:\n\tpass\n",
        "bad.gd": "extends Node\nvar broken =\n",
    })
    monkeypatch.setattr("godot_coder.corpus._find_godot", lambda: "godot.exe")
    monkeypatch.setattr("godot_coder.corpus._godot_version", lambda executable: "4.7.test")

    def fake_managed(command: list[str], **kwargs):
        if "--check-only" in command:
            output = (
                "SCRIPT ERROR: Parse Error: Expected expression after '='.\n"
                "   at: GDScript::reload (res://bad.gd:2)\n"
            )
            rc = 1
        else:
            output = "Godot Engine v4.7\nERROR: Failed to load script: res://bad.gd (Parse Error suppressed)\n"
            rc = 1
        return ManagedProcessResult(
            command=command, return_code=rc, output=output,
            timed_out=False, duration_seconds=0.1, pid=None, termination_attempted=False,
        )

    monkeypatch.setattr("godot_coder.corpus.run_managed_process", fake_managed)
    report = validate_and_finalize(root, minimum_accepted=0)
    assert report["failed"] == 1
    assert report["classifications"]["syntax_error"] == 1
    assert report["context_warnings"] == 1  # good.gd stays a verified warning
    assert report["prepared"] == 1  # only good.gd lands in prepared/
