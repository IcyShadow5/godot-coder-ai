from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from godot_coder.local_sources import (
    ImportPlan,
    ImportProgress,
    _copy_project,
    _import_one,
    _recover_recorded_validation,
    _registry_source,
    _validate_project,
    ProjectAudit,
    ProjectValidationResult,
)
from godot_coder.process_control import (
    ManagedProcessResult,
    _terminate_windows_process_tree,
    _windows_job_assign,
    _windows_job_close,
    _windows_job_create,
    _windows_job_terminate,
    process_is_alive,
    run_managed_process,
)
from godot_coder.progress_events import ProgressEmitter, parse_event_line


def _minimal_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.godot").write_text('config_version=5\n[application]\nconfig/name="Watchdog"\n', encoding="utf-8")
    (root / "main.gd").write_text("extends Node\n", encoding="utf-8")


def test_managed_process_timeout_terminates_descendant_tree(tmp_path: Path) -> None:
    child_pid: list[int] = []
    code = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print(p.pid, flush=True); time.sleep(60)"
    )
    result = run_managed_process(
        [sys.executable, "-u", "-c", code],
        cwd=tmp_path,
        timeout_seconds=0.5,
        heartbeat_seconds=0.1,
        on_line=lambda line: child_pid.append(int(line)) if line.isdigit() else None,
    )
    assert result.timed_out is True
    assert result.termination_attempted is True
    assert child_pid
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and process_is_alive(child_pid[0]):
        time.sleep(0.05)
    assert process_is_alive(child_pid[0]) is False


def test_validate_project_uses_isolated_workspace_and_cleans_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "persistent-corpus-copy"
    _minimal_project(project)
    workspace_root = tmp_path / "reports" / "validation_work"
    seen_workspace: list[Path] = []

    monkeypatch.setattr("godot_coder.local_sources._find_godot", lambda: "fake-godot")

    def fake_run(command, **kwargs):
        workspace = Path(command[command.index("--path") + 1])
        seen_workspace.append(workspace)
        assert workspace != project
        assert workspace_root in workspace.parents
        (workspace / ".godot").mkdir()
        (workspace / ".godot" / "editor.lock").write_text("temporary", encoding="utf-8")
        kwargs["on_start"](4242)
        kwargs["on_line"]("Godot Engine test")
        kwargs["on_heartbeat"](6.0, "Importing assets")
        return ManagedProcessResult(
            command=list(command), return_code=0, output="Godot Engine test", timed_out=False,
            duration_seconds=0.1, pid=4242, termination_attempted=False,
        )

    monkeypatch.setattr("godot_coder.local_sources.run_managed_process", fake_run)
    status, error, output = _validate_project(
        project,
        workspace_root=workspace_root,
        active_record_path=tmp_path / "reports" / "active_validation.json",
    )
    assert status == "passed"
    assert error is None
    assert "Godot Engine test" in output
    assert seen_workspace and not seen_workspace[0].exists()
    assert not (project / ".godot").exists()
    assert (project / "main.gd").read_text(encoding="utf-8") == "extends Node\n"


def test_validate_project_retries_once_after_timeout_and_reports_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _minimal_project(project)
    lines: list[str] = []
    progress = ImportProgress(
        ProgressEmitter(sink=lines.append),
        [ImportPlan(project=project, source_item=project, project_name="Watchdog", script_count=1)],
    )
    calls = 0
    monkeypatch.setattr("godot_coder.local_sources._find_godot", lambda: "fake-godot")

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["on_start"](5000 + calls)
        if calls == 1:
            kwargs["on_heartbeat"](5.0, "Loading GDExtension")
            return ManagedProcessResult(
                command=list(command), return_code=-9, output="Loading GDExtension", timed_out=True,
                duration_seconds=5.0, pid=5001, termination_attempted=True,
            )
        return ManagedProcessResult(
            command=list(command), return_code=0, output="Import complete", timed_out=False,
            duration_seconds=0.2, pid=5002, termination_attempted=False,
        )

    monkeypatch.setattr("godot_coder.local_sources.run_managed_process", fake_run)
    status, error, _ = _validate_project(
        project,
        progress=progress,
        project_index=1,
        workspace_root=tmp_path / "validation",
        active_record_path=tmp_path / "active.json",
    )
    events = [event for line in lines if (event := parse_event_line(line))]
    assert calls == 2
    assert status == "passed_with_warnings"
    assert error is None
    assert any("The process tree was terminated" in str(event.get("message")) for event in events)
    assert any(event.get("validation_fallback") is True for event in events)
    assert events[-1]["phase_status"] == "passed_with_warnings"


def test_validate_project_fails_cleanly_after_two_timeouts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    _minimal_project(project)
    monkeypatch.setattr("godot_coder.local_sources._find_godot", lambda: "fake-godot")
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["on_start"](6000 + calls)
        return ManagedProcessResult(
            command=list(command), return_code=-9, output="still busy", timed_out=True,
            duration_seconds=1.0, pid=6000 + calls, termination_attempted=True,
        )

    monkeypatch.setattr("godot_coder.local_sources.run_managed_process", fake_run)
    status, error, _ = _validate_project(
        project,
        workspace_root=tmp_path / "validation",
        active_record_path=tmp_path / "active.json",
    )
    assert calls == 2
    assert status == "failed"
    assert error and "all 1 trainable scripts failed" in error
    assert not (tmp_path / "active.json").exists()


def test_copy_project_reuses_existing_deterministic_copy_without_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _minimal_project(source)
    _minimal_project(destination)
    marker = destination / "keep.marker"
    marker.write_text("existing", encoding="utf-8")
    monkeypatch.setattr("godot_coder.local_sources._cleanup_generated_copy", lambda _path: ["locked cache"])
    _copy_project(source, destination, reuse_existing=True)
    assert marker.read_text(encoding="utf-8") == "existing"


def test_recorded_validation_recovery_only_kills_verified_process(tmp_path: Path) -> None:
    workspace = tmp_path / "validation-workspace"
    workspace.mkdir()
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "godot", "--path", str(workspace)],
        start_new_session=True,
    )
    record = tmp_path / "reports" / "local_sources" / "active_validation.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"pid": process.pid, "workspace": str(workspace)}), encoding="utf-8")
    try:
        assert _recover_recorded_validation(tmp_path) is True
        process.wait(timeout=5)
        assert not record.exists()
    finally:
        if process.poll() is None:
            process.kill()


def test_managed_process_can_abort_on_known_output(tmp_path: Path) -> None:
    result = run_managed_process(
        [sys.executable, "-u", "-c", "import time; print('FATAL_MARKER', flush=True); time.sleep(60)"],
        cwd=tmp_path,
        timeout_seconds=30,
        heartbeat_seconds=0.1,
        abort_on_line=lambda line: "known failure" if "FATAL_MARKER" in line else None,
    )
    assert result.aborted is True
    assert result.abort_reason == "known failure"
    assert result.termination_attempted is True
    assert result.duration_seconds < 10


def test_known_mono_editor_failure_switches_to_file_parser_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _minimal_project(project)
    (project / "second.gd").write_text("extends Node\n", encoding="utf-8")
    lines: list[str] = []
    progress = ImportProgress(
        ProgressEmitter(sink=lines.append),
        [ImportPlan(project=project, source_item=project, project_name="Watchdog", script_count=2)],
    )
    monkeypatch.setattr("godot_coder.local_sources._find_godot", lambda: "fake-godot")
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        command = list(command)
        commands.append(command)
        kwargs["on_start"](7000 + len(commands))
        if "--import" in command:
            line = 'ERROR: EditorSettings not instantiated yet when getting setting "export/android/shutdown_adb_on_exit".'
            kwargs["on_line"](line)
            reason = kwargs["abort_on_line"](line)
            return ManagedProcessResult(
                command=command, return_code=1, output=line, timed_out=False,
                duration_seconds=0.1, pid=7001, termination_attempted=True,
                aborted=True, abort_reason=reason,
            )
        kwargs["on_line"]("Godot Engine test")
        return ManagedProcessResult(
            command=command, return_code=0, output="Godot Engine test", timed_out=False,
            duration_seconds=0.05, pid=7000 + len(commands), termination_attempted=False,
        )

    monkeypatch.setattr("godot_coder.local_sources.run_managed_process", fake_run)
    result = _validate_project(
        project,
        progress=progress,
        project_index=1,
        workspace_root=tmp_path / "validation",
        active_record_path=tmp_path / "active.json",
    )
    status, error, _ = result
    events = [event for line in lines if (event := parse_event_line(line))]
    assert status == "passed_with_warnings"
    assert error is None
    assert result.mode == "gdscript_check"
    assert result.checked_files == 2
    assert result.failed_files == []
    assert len([command for command in commands if "--import" in command]) == 1
    assert len([command for command in commands if "--check-only" in command]) == 2
    assert any(event.get("validation_fallback") is True for event in events)
    assert any("ADB" in str(event.get("message")) or "Parser fallback check" in str(event.get("message")) for event in events)


def test_file_parser_fallback_reports_and_excludes_only_failed_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _minimal_project(project)
    (project / "broken.gd").write_text("extends Node\nfunc broken(\n", encoding="utf-8")
    monkeypatch.setattr("godot_coder.local_sources._find_godot", lambda: "fake-godot")

    def fake_run(command, **kwargs):
        command = list(command)
        kwargs["on_start"](8000)
        if "--import" in command:
            line = 'ERROR: EditorSettings not instantiated yet when getting setting "export/android/shutdown_adb_on_exit".'
            kwargs["on_line"](line)
            return ManagedProcessResult(
                command=command, return_code=1, output=line, timed_out=False,
                duration_seconds=0.1, pid=8000, termination_attempted=True,
                aborted=True, abort_reason="known editor failure",
            )
        script = command[command.index("--script") + 1]
        if script.endswith("broken.gd"):
            line = "SCRIPT ERROR: Parse Error: Expected parameter name."
            kwargs["on_line"](line)
            return ManagedProcessResult(
                command=command, return_code=1, output=line, timed_out=False,
                duration_seconds=0.05, pid=8001, termination_attempted=False,
            )
        kwargs["on_line"]("parse ok")
        return ManagedProcessResult(
            command=command, return_code=0, output="parse ok", timed_out=False,
            duration_seconds=0.05, pid=8002, termination_attempted=False,
        )

    monkeypatch.setattr("godot_coder.local_sources.run_managed_process", fake_run)
    result = _validate_project(
        project,
        workspace_root=tmp_path / "validation",
        active_record_path=tmp_path / "active.json",
    )
    assert result.status == "passed_with_warnings"
    assert result.checked_files == 2
    assert result.failed_files == ["broken.gd"]



def test_managed_process_idle_timeout_stops_silent_process(tmp_path: Path) -> None:
    result = run_managed_process(
        [sys.executable, "-u", "-c", "import time; print('started', flush=True); time.sleep(60)"],
        cwd=tmp_path,
        timeout_seconds=30,
        idle_timeout_seconds=0.4,
        heartbeat_seconds=0.1,
    )
    assert result.timed_out is True
    assert result.idle_timed_out is True
    assert result.termination_attempted is True
    assert result.duration_seconds < 10


def test_registry_excludes_oversized_and_parser_failed_files() -> None:
    audit = ProjectAudit(
        project_name="Pennyshire",
        project_root="Pennyshire",
        source_item="private.zip",
        source_sha256="a" * 64,
        godot_features=["4.7"],
        gd_files=3,
        gd_bytes=100,
        gd_lines=10,
        estimated_bpe_tokens=32,
        trainable_gd_files=1,
        trainable_gd_bytes=50,
        trainable_estimated_bpe_tokens=16,
        oversized_gd_files=["scripts/huge.gd"],
        addon_files=0,
        generated_files=0,
        executable_files=0,
        secret_hits=[],
        static_warnings=[],
        known_logs={},
        ownership_confirmed=True,
        redistribution_allowed=False,
        parser_checked_files=2,
        parser_failed_files=["scripts/broken.gd"],
        validation_mode="gdscript_check",
        validation_status="passed_with_warnings",
    )
    source = _registry_source(audit, "local-pennyshire")
    assert source["exclude_paths"] == ["addons", "scripts/huge.gd", "scripts/broken.gd"]



def test_partial_parser_fallback_keeps_valid_scripts_trainable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "private-project"
    _minimal_project(project)
    (project / "broken.gd").write_text("extends Node\nfunc broken(\n", encoding="utf-8")
    monkeypatch.setattr(
        "godot_coder.local_sources._validate_project",
        lambda *_args, **_kwargs: ProjectValidationResult(
            status="passed_with_warnings",
            error=None,
            output="fallback",
            failed_files=["broken.gd"],
            checked_files=2,
            mode="gdscript_check",
            infrastructure_failure="known editor failure",
        ),
    )
    audit = _import_one(
        tmp_path,
        project,
        project,
        ownership_confirmed=True,
    )
    assert audit.enabled_for_training is True
    assert audit.validation_status == "passed_with_warnings"
    assert audit.validation_mode == "gdscript_check"
    assert audit.parser_failed_files == ["broken.gd"]
    assert audit.trainable_gd_files == 1


def test_real_process_switches_from_editor_failure_to_parser_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _minimal_project(project)
    fake_program = tmp_path / "fake_godot.py"
    fake_program.write_text(
        "import sys,time\n"
        "if '--import' in sys.argv:\n"
        " print('ERROR: Unable to start the timer because it is not inside the scene tree.', flush=True)\n"
        " print('ERROR: EditorSettings not instantiated yet when getting setting \"export/android/shutdown_adb_on_exit\".', flush=True)\n"
        " time.sleep(60)\n"
        "elif '--check-only' in sys.argv:\n"
        " print('parser ok', flush=True)\n"
        " sys.exit(0)\n"
        "sys.exit(2)\n",
        encoding="utf-8",
    )
    if sys.platform == "win32":
        # Match the real Windows setup, where GodotShim exposes godot.CMD.
        # A Unix shebang-only file cannot be executed directly by CreateProcess.
        fake_godot = tmp_path / "fake-godot.cmd"
        fake_godot.write_text(
            f'@echo off\r\n"{sys.executable}" "{fake_program}" %*\r\n',
            encoding="utf-8",
        )
    else:
        fake_godot = tmp_path / "fake-godot"
        fake_godot.write_text(
            "#!/usr/bin/env sh\n"
            f'exec "{sys.executable}" "{fake_program}" "$@"\n',
            encoding="utf-8",
        )
        fake_godot.chmod(0o755)
    monkeypatch.setattr("godot_coder.local_sources._find_godot", lambda: str(fake_godot))
    started = time.monotonic()
    result = _validate_project(
        project,
        workspace_root=tmp_path / "validation",
        active_record_path=tmp_path / "active.json",
    )
    assert time.monotonic() - started < 15
    assert result.status == "passed_with_warnings"
    assert result.mode == "gdscript_check"
    assert result.failed_files == []
    assert "EditorSettings not instantiated yet" in result.output


def test_recorded_validation_recovery_never_reports_success_while_pid_is_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "validation-workspace"
    workspace.mkdir()
    record = tmp_path / "reports" / "local_sources" / "active_validation.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"pid": 4242, "workspace": str(workspace)}), encoding="utf-8")

    monkeypatch.setattr("godot_coder.local_sources.process_command_line", lambda _pid: f"godot --path {workspace}")
    monkeypatch.setattr("godot_coder.local_sources.process_is_alive", lambda _pid: True)
    monkeypatch.setattr("godot_coder.local_sources.terminate_process_tree", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("godot_coder.local_sources.time.sleep", lambda _seconds: None)
    ticks = iter([0.0, 4.0, 4.0])
    monkeypatch.setattr("godot_coder.local_sources.time.monotonic", lambda: next(ticks, 4.0))

    assert _recover_recorded_validation(tmp_path) is False
    assert record.exists()





def test_job_create_returns_none_on_non_windows(monkeypatch):
    monkeypatch.setattr("os.name", "posix")
    assert _windows_job_create() is None


def test_job_assign_returns_false_when_process_not_found(monkeypatch):
    monkeypatch.setattr("os.name", "nt")
    monkeypatch.setattr(
        "godot_coder.process_control._windows_job_assign",
        lambda _job, _pid: False,
    )
    assert _windows_job_assign(42, 99999) is False


def test_job_close_removes_handle_from_global_list(monkeypatch):
    monkeypatch.setattr("os.name", "nt")
    import godot_coder.process_control as pc
    pc._WIN_JOB_HANDLES.clear()
    pc._WIN_JOB_HANDLES.append(42)
    pc._WIN_JOB_HANDLES.append(43)
    assert pc._WIN_JOB_HANDLES == [42, 43]

    _windows_job_close(42)
    assert pc._WIN_JOB_HANDLES == [43]

    _windows_job_close(43)
    assert pc._WIN_JOB_HANDLES == []


def test_job_close_handles_nonexistent_handle_gracefully(monkeypatch):
    monkeypatch.setattr("os.name", "nt")
    import godot_coder.process_control as pc
    pc._WIN_JOB_HANDLES.clear()
    # Should not raise when handle is not in the list
    _windows_job_close(999)
    assert pc._WIN_JOB_HANDLES == []


def test_terminate_job_returns_false_on_failure(monkeypatch):
    monkeypatch.setattr("os.name", "nt")
    monkeypatch.setattr(
        "godot_coder.process_control._windows_job_terminate",
        lambda _handle: False,
    )
    assert _windows_job_terminate(42) is False


def test_windows_tree_termination_forces_and_waits_for_exact_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    waited: list[tuple[int, float, object]] = []

    class FakeKernel32:
        def __init__(self) -> None:
            self.closed: list[object] = []

        def CloseHandle(self, handle) -> None:
            self.closed.append(handle)

    kernel32 = FakeKernel32()
    handle = object()
    monkeypatch.setattr(
        "godot_coder.process_control._windows_process_handle",
        lambda pid, access: (kernel32, handle),
    )
    monkeypatch.setattr(
        "godot_coder.process_control._windows_wait_for_exit",
        lambda pid, seconds, opened=None: waited.append((pid, seconds, opened)) or True,
    )
    monkeypatch.setattr(
        "godot_coder.process_control.subprocess.run",
        lambda command, **kwargs: commands.append([str(value) for value in command]),
    )

    assert _terminate_windows_process_tree(4242, force=True, wait_seconds=8.0) is True
    assert commands == [["taskkill", "/PID", "4242", "/T", "/F"]]
    assert waited == [(4242, 8.0, (kernel32, handle))]
    assert kernel32.closed == [handle]
