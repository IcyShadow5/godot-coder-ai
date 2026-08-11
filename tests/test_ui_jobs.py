"""Tests for ui/jobs.py — the Job dataclass and the JobManager.

The E2E happy paths (structured progress, exports, stall watchdog) live in
test_job_progress.py. This file covers the parts that file does not reach:
level inference, snapshot fallbacks, restart recovery, export validation,
the legacy text-progress parsers, progress merging and the terminal states.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import types
import uuid
from pathlib import Path

import pytest

from godot_coder.progress_events import EVENT_SCHEMA_VERSION, parse_event_line, serialize_event
from godot_coder.ui import jobs as jobs_module
from godot_coder.ui.jobs import Job, JobManager, _LOSS_SAMPLE_LIMIT, _level_from_text, _project_sort_key


def _wait_for_terminal(manager: JobManager, timeout: float = 8.0) -> dict[str, object]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = manager.current()
        if snapshot and snapshot["status"] in {"completed", "failed", "stopped"}:
            return snapshot
        time.sleep(0.03)
    raise AssertionError("job did not finish")


def _make_job(tmp_path: Path, **overrides: object) -> Job:
    fields: dict[str, object] = {
        "id": uuid.uuid4().hex[:12],
        "kind": "test-kind",
        "command": [sys.executable, "-u", "-c", "print('x')"],
        "cwd": str(tmp_path),
    }
    fields.update(overrides)
    return Job(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #


def test_level_from_text_detects_error_keywords() -> None:
    for line in (
        "Traceback (most recent call last):",
        "something errored",
        "script failed to parse",
        "an exception was raised",
    ):
        assert _level_from_text(line) == "error", line


def test_level_from_text_detects_warning_keywords() -> None:
    for line in ("warning: deprecated call", "warn: low disk", "quarantined 2 files"):
        assert _level_from_text(line) == "warning", line


def test_level_from_text_corpus_validate_wins_over_keywords() -> None:
    # The structured corpus-validate marker is always info, even when the
    # line also contains an error keyword.
    assert _level_from_text("validate=3/5 error-ish") == "info"


def test_level_from_text_defaults_to_info() -> None:
    assert _level_from_text("normal progress line") == "info"
    assert _level_from_text("") == "info"


def test_project_sort_key_orders_by_index_then_name() -> None:
    projects = [
        {"project_index": 2, "project_name": "b"},
        {"project_index": 1, "project_name": "z"},
        {"project_index": 1, "project_name": "a"},
    ]
    ordered = sorted(projects, key=_project_sort_key)
    assert [p["project_name"] for p in ordered] == ["a", "z", "b"]


def test_project_sort_key_missing_or_non_int_index_sorts_last() -> None:
    assert _project_sort_key({"project_index": 1}) < _project_sort_key({"project_name": "x"})
    # JSON round trips can turn ints into floats; those must not win.
    assert _project_sort_key({"project_index": 1}) < _project_sort_key({"project_index": 1.0})
    assert _project_sort_key({"project_index": 0}) < _project_sort_key({"project_name": "x"})


# --------------------------------------------------------------------------- #
# Job dataclass
# --------------------------------------------------------------------------- #


def test_job_elapsed_seconds_uses_started_and_now(tmp_path: Path) -> None:
    job = _make_job(tmp_path)
    assert job.elapsed_seconds() == 0.0  # never started

    job.started_at = 100.0
    assert job.elapsed_seconds(now=105.0) == 5.0
    assert job.elapsed_seconds(now=99.0) == 0.0  # clamped, no negative times

    job.finished_at = 103.0
    assert job.elapsed_seconds(now=999.0) == 3.0  # finished_at wins


def test_job_snapshot_progress_falls_back_to_step_and_max_steps(tmp_path: Path) -> None:
    job = _make_job(tmp_path, step=3, max_steps=10)
    assert job.snapshot()["progress"] == 0.3
    job.step = 12
    assert job.snapshot()["progress"] == 1.0  # capped
    job.max_steps = None
    assert job.snapshot()["progress"] is None


def test_job_snapshot_keeps_explicit_progress_and_elapsed(tmp_path: Path) -> None:
    job = _make_job(tmp_path)
    job.progress_state = {"overall_progress": 0.7, "elapsed_seconds": 12.3}
    snapshot = job.snapshot()
    assert snapshot["progress"] == 0.7
    assert snapshot["progress_state"]["elapsed_seconds"] == 12.3
    assert snapshot["event_schema_version"] == EVENT_SCHEMA_VERSION


def test_job_snapshot_sorts_projects_and_masks_command(tmp_path: Path) -> None:
    job = _make_job(
        tmp_path,
        command=["python", "-c", "api_key=sk-abcdefghijklmnopqrstuvwxyz123456"],
        extra_env={"GODOT_CODER_TOGGLE": "secret-value"},
    )
    job.progress_state = {
        "projects": [
            {"project_index": 2, "project_name": "Second"},
            {"project_index": 1, "project_name": "First"},
        ]
    }
    snapshot = job.snapshot()
    assert [p["project_name"] for p in snapshot["progress_state"]["projects"]] == ["First", "Second"]
    assert "extra_env" not in snapshot
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in json.dumps(snapshot)
    assert "[REDACTED]" in json.dumps(snapshot["command"])


def test_job_from_snapshot_round_trip(tmp_path: Path) -> None:
    original = _make_job(
        tmp_path,
        id="abc123def456",
        kind="corpus-import",
        command=["python", "-u", "-m", "godot_coder.corpus"],
        status="failed",
        created_at=1000.0,
        started_at=1001.0,
        finished_at=1002.0,
        return_code=1,
        pid=42,
        step=3,
        max_steps=5,
    )
    original.logs.append("some log")
    original.log_records.append({"record_type": "log", "text": "some log"})
    original.events.append({"event": "job_finished"})
    original.progress_state = {"job_status": "failed", "projects": []}
    original.last_successful_step = {"phase": "inventory"}

    restored = Job.from_snapshot(original.snapshot())
    assert restored.id == "abc123def456"
    assert restored.kind == "corpus-import"
    assert restored.status == "failed"
    assert restored.created_at == 1000.0
    assert restored.started_at == 1001.0
    assert restored.finished_at == 1002.0
    assert restored.return_code == 1
    assert restored.pid == 42
    assert restored.step == 3
    assert restored.max_steps == 5
    assert list(restored.logs) == ["some log"]
    assert restored.log_records[0]["text"] == "some log"
    assert restored.events[0]["event"] == "job_finished"
    assert restored.progress_state["job_status"] == "failed"
    assert restored.progress_state["projects"] == []
    assert "elapsed_seconds" in restored.progress_state  # added by snapshot()
    assert restored.last_successful_step == {"phase": "inventory"}


def test_job_from_snapshot_defaults_and_filters(tmp_path: Path) -> None:
    job = Job.from_snapshot({})
    assert len(job.id) == 12
    assert all(char in "0123456789abcdef" for char in job.id)
    assert job.kind == "unknown"
    assert job.status == "failed"
    assert job.command == []
    assert job.cwd == ""

    # Non-dict entries in log_records/events are dropped.
    job = Job.from_snapshot({
        "log_records": [{"ok": 1}, "junk"],
        "events": ["junk", {"event": "x"}],
        "command": [1, "two"],
        "logs": [3, "three"],
    })
    assert list(job.log_records) == [{"ok": 1}]
    assert list(job.events) == [{"event": "x"}]
    assert job.command == ["1", "two"]
    assert list(job.logs) == ["3", "three"]


# --------------------------------------------------------------------------- #
# JobManager basics
# --------------------------------------------------------------------------- #


def test_fresh_manager_has_no_current_history_or_busy(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    assert manager.current() is None
    assert manager.history() == []
    assert manager.is_busy() is False


def test_persist_snapshot_writes_atomically(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    manager._persist_snapshot(job)
    path = manager._snapshot_path(job)
    assert json.loads(path.read_text(encoding="utf-8"))["id"] == job.id
    assert not list(manager.state_dir.glob("*.tmp"))


def _training_loss_event(step: int, loss: float) -> dict[str, object]:
    line = serialize_event({"event": "train_loss", "step": step, "loss": loss, "job_id": "job123"})
    event = parse_event_line(line, job_id="job123")
    assert event is not None
    return event


def test_train_loss_event_populates_loss_history(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    manager._append_event(job, _training_loss_event(3, 0.5))
    manager._append_event(job, _training_loss_event(4, 0.45))
    state = job.progress_state
    assert state["loss_samples"] == [{"step": 3, "loss": 0.5}, {"step": 4, "loss": 0.45}]
    assert state["current_loss"] == 0.45
    assert state["loss_step"] == 4
    snapshot = job.snapshot()
    assert snapshot["progress_state"]["loss_samples"] == state["loss_samples"]


def test_loss_samples_ring_stays_bounded(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    for step in range(1, _LOSS_SAMPLE_LIMIT + 50):
        manager._append_event(job, _training_loss_event(step, step / 1000.0))
    samples = job.progress_state["loss_samples"]
    assert len(samples) == _LOSS_SAMPLE_LIMIT
    assert samples[0]["step"] == 50
    assert samples[-1]["step"] == _LOSS_SAMPLE_LIMIT + 49
    assert job.progress_state["current_loss"] == (_LOSS_SAMPLE_LIMIT + 49) / 1000.0


def test_train_loss_event_without_loss_or_step_is_ignored(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    manager._append_event(job, _training_loss_event(1, 0.5))
    line = serialize_event({"event": "train_loss", "step": 2})
    event = parse_event_line(line)
    assert event is not None and event.get("loss") is None
    manager._append_event(job, event)
    line = serialize_event({"event": "train_loss", "loss": 0.5})
    event = parse_event_line(line)
    assert event is not None and event.get("step") is None
    manager._append_event(job, event)
    assert job.progress_state["current_loss"] == 0.5
    assert job.progress_state["loss_step"] == 1
    assert job.progress_state["loss_samples"] == [{"step": 1, "loss": 0.5}]


def test_non_loss_events_leave_loss_history_untouched(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    manager._append_event(job, _training_loss_event(1, 0.5))
    line = serialize_event({"event": "phase_status", "phase": "static_analysis", "phase_status": "passed"})
    event = parse_event_line(line)
    assert event is not None
    manager._append_event(job, event)
    assert job.progress_state["loss_samples"] == [{"step": 1, "loss": 0.5}]
    assert job.progress_state["current_loss"] == 0.5


def test_start_sets_env_and_snapshot_fields_e2e(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    script = (
        "import os\n"
        "print(os.environ.get('GODOT_CODER_JOB_ID'), flush=True)\n"
        "print(os.environ.get('GODOT_CODER_EXTRA'), flush=True)\n"
    )
    started = manager.start(
        "test-kind",
        ["-c", script],
        max_steps=7,
        extra_env={"GODOT_CODER_EXTRA": "extra-value"},
    )
    assert started["status"] == "starting"
    assert started["max_steps"] == 7
    assert started["command"][0] == sys.executable
    assert started["command"][1] == "-u"
    snapshot = _wait_for_terminal(manager)
    assert snapshot["status"] == "completed"
    logs = "\n".join(snapshot["logs"])
    assert started["id"] in logs
    assert "extra-value" in logs
    assert snapshot["max_steps"] == 7
    assert snapshot["return_code"] == 0


def test_start_raises_when_busy(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    # Long sleep so the child is still alive when the second start() runs.
    manager.start("test-kind", ["-c", "import time; time.sleep(5)"])
    with pytest.raises(RuntimeError, match="already running"):
        manager.start("test-kind", ["-c", "print('nope')"])
    # Wait for the child to spawn, then clean it up so no process leaks.
    deadline = time.time() + 8
    while time.time() < deadline:
        snapshot = manager.current()
        if snapshot and snapshot["status"] in {"running", "completed"}:
            break
        time.sleep(0.03)
    manager.stop()
    assert _wait_for_terminal(manager)["status"] == "stopped"


def test_stop_without_running_process(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    assert manager.stop() is None  # no job at all

    # A finished job has a dead process handle -> stop() just returns the snapshot.
    manager.start("test-kind", ["-c", "print('quick')"])
    _wait_for_terminal(manager)
    snapshot = manager.stop()
    assert snapshot is not None
    assert snapshot["status"] == "completed"


def test_export_path_validation(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    for bad in ("bad id!", "ab", "x/y/z"):
        with pytest.raises(ValueError, match="invalid job id"):
            manager.export_path(bad, "text")

    unknown = "z" * 12
    with pytest.raises(FileNotFoundError, match="unknown job"):
        manager.export_path(unknown, "text")

    # Known job (in history) but no export files on disk.
    job = _make_job(tmp_path)
    manager._history.append(job)
    with pytest.raises(ValueError, match="format must be text or jsonl"):
        manager.export_path(job.id, "html")
    with pytest.raises(FileNotFoundError, match="log export not found"):
        manager.export_path(job.id, "text")


def test_export_path_returns_existing_files(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    started = manager.start("test-kind", ["-c", "print('export me')"])
    _wait_for_terminal(manager)
    text = manager.export_path(started["id"], "text")
    jsonl = manager.export_path(started["id"], "jsonl")
    assert "export me" in text.read_text(encoding="utf-8")
    assert jsonl.exists()
    assert str(text).endswith(".log.txt")
    assert str(jsonl).endswith(".log.jsonl")


# --------------------------------------------------------------------------- #
# restart recovery (_load_history)
# --------------------------------------------------------------------------- #


def test_load_history_marks_active_jobs_failed_after_restart(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path, status="running", created_at=time.time())
    manager._persist_snapshot(job)

    restored = JobManager(tmp_path)
    snapshot = restored.current()
    assert snapshot is not None
    assert snapshot["id"] == job.id
    assert snapshot["status"] == "failed"
    assert snapshot["return_code"] == -2
    assert "restarted" in snapshot["progress_state"]["failure_reason"]
    assert any("Studio restart detected" in record["text"] for record in snapshot["log_records"])

    # The on-disk snapshot was updated too.
    payload = json.loads(manager._snapshot_path(job).read_text(encoding="utf-8"))
    assert payload["status"] == "failed"


def test_load_history_skips_corrupted_snapshots(tmp_path: Path) -> None:
    first = JobManager(tmp_path)
    job = _make_job(tmp_path, status="completed", created_at=time.time())
    first._persist_snapshot(job)
    (first.state_dir / "broken.snapshot.json").write_text("not json {{{", encoding="utf-8")
    (first.state_dir / "empty.snapshot.json").write_text("[1, 2, 3]", encoding="utf-8")

    restored = JobManager(tmp_path)
    assert restored.current() is not None
    assert restored.current()["id"] == job.id
    assert len(restored.history()) == 1


def test_load_history_cleans_stale_files_beyond_last_20(tmp_path: Path) -> None:
    first = JobManager(tmp_path)
    ids: list[str] = []
    for index in range(25):
        job = _make_job(
            tmp_path,
            id=f"{index:012d}",
            kind="test-kind",
            status="completed",
            created_at=float(1000 + index),
        )
        first._persist_snapshot(job)
        (first.state_dir / f"{job.id}.log.txt").write_text(f"job {index}", encoding="utf-8")
        ids.append(job.id)

    restored = JobManager(tmp_path)
    assert len(restored.history()) == 20
    remaining = {path.stem.replace(".snapshot", "") for path in restored.state_dir.glob("*.snapshot.json")}
    assert remaining == set(ids[5:])
    # The oldest five sidecar logs were deleted, the kept ones survive.
    assert not (restored.state_dir / f"{ids[0]}.log.txt").exists()
    assert (restored.state_dir / f"{ids[-1]}.log.txt").exists()


# --------------------------------------------------------------------------- #
# legacy text progress parsers
# --------------------------------------------------------------------------- #


def test_update_legacy_progress_parses_all_step_patterns(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    cases = {
        "training step=42 loss=1.2": 42,
        "validation step=7 loss=0.9": 7,
        "[12] epoch 3": 12,
        "benchmark=5/10 pass": 5,
        "source=3/7 download": 3,
        "validate=2/3 checked": 2,
        "profile_probe=1/2 measured": 1,
        "autotune=4/5 variant": 4,
    }
    for line, expected in cases.items():
        job.step = None
        manager._update_legacy_progress(job, line)
        assert job.step == expected, line
        assert job.progress_state == {}  # plain step lines touch nothing else


def test_update_legacy_progress_local_import_sets_phase(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    manager._update_legacy_progress(job, "local_import=1/2 item=one phase=inspect")
    assert job.step == 1
    assert job.progress_state["project_index"] == 1
    assert job.progress_state["project_total"] == 2
    assert job.progress_state["overall_progress"] == 0.5
    assert job.progress_state["phase"] == "input_detection"
    assert job.progress_state["legacy_text_progress"] is True


def test_update_legacy_progress_local_project_parsing(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    manager._update_legacy_progress(
        job,
        "local_project='Old Demo' scripts=3 validation=passed enabled=True",
    )
    projects = job.progress_state["projects"]
    assert len(projects) == 1
    project = projects[0]
    assert project["project_name"] == "Old Demo"
    assert project["scripts_found"] == 3
    assert project["project_status"] == "passed"
    assert project["validation_status"] == "passed"
    assert project["enabled_for_training"] is True

    # Disabled projects come out as quarantined.
    manager._update_legacy_progress(
        job,
        "local_project='Quarantined One' scripts=1 validation=error enabled=False",
    )
    assert job.progress_state["projects"][1]["project_status"] == "quarantined"
    assert job.progress_state["projects"][1]["enabled_for_training"] is False


def test_update_legacy_progress_skipped_once_structured_events_exist(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    job.events.append({"event": "local_project_progress"})
    manager._update_legacy_progress(job, "local_import=1/2 item=one")
    assert job.progress_state == {}  # structured events take over


def test_update_legacy_progress_ignores_unrelated_lines(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path, step=5)
    manager._update_legacy_progress(job, "just some output")
    assert job.step == 5
    assert job.progress_state == {}


# --------------------------------------------------------------------------- #
# structured event progress (_update_progress)
# --------------------------------------------------------------------------- #


def test_update_progress_tracks_last_event_and_computes_overall(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    manager._update_progress(job, {
        "schema_version": 1,
        "event": "local_project_progress",
        "timestamp": "2026-01-01T00:00:00Z",
        "project_index": 2,
        "project_total": 4,
        "phase": "inventory",
        "phase_status": "passed",
    })
    state = job.progress_state
    assert state["schema_version"] == 1
    assert state["last_event"] == "local_project_progress"
    assert state["project_index"] == 2
    assert state["project_total"] == 4
    assert state["overall_progress"] == 0.5  # computed from index/total
    assert state["phase"] == "inventory"


def test_update_progress_merges_projects_and_phases(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    manager._update_progress(job, {
        "project_index": 1, "project_name": "Demo",
        "phase": "inventory", "phase_status": "passed", "message": "inventoried",
    })
    manager._update_progress(job, {
        "project_index": 1, "project_name": "Demo",
        "phase": "static_analysis", "phase_status": "failed", "failed": 1,
    })
    manager._update_progress(job, {
        "project_index": 2, "project_name": "Other",
        "phase": "inventory", "phase_status": "passed",
    })
    projects = job.progress_state["projects"]
    assert len(projects) == 2
    demo = next(p for p in projects if p["project_name"] == "Demo")
    assert [phase["phase"] for phase in demo["phases"]] == ["inventory", "static_analysis"]
    assert demo["failed"] == 1
    assert projects[1]["project_name"] == "Other"


def test_update_progress_records_last_successful_step(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    manager._update_progress(job, {
        "project_index": 1, "project_name": "Demo",
        "phase": "godot_validation", "phase_status": "passed_with_warnings",
        "current_file": "main.gd", "message": "ok-ish",
    })
    assert job.last_successful_step is not None
    assert job.last_successful_step["phase"] == "godot_validation"
    assert job.last_successful_step["project_name"] == "Demo"

    # A failed phase must not overwrite the last success.
    manager._update_progress(job, {
        "project_index": 1, "phase": "quarantine_decision", "phase_status": "failed",
    })
    assert job.last_successful_step["phase"] == "godot_validation"


def test_update_progress_replaces_projects_from_event(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    manager._update_progress(job, {
        "projects": [
            {"project_index": 1, "project_name": "A", "project_status": "passed"},
            {"project_index": 2, "project_name": "B", "project_status": "quarantined"},
        ]
    })
    assert [p["project_name"] for p in job.progress_state["projects"]] == ["A", "B"]


# --------------------------------------------------------------------------- #
# log/event appending + drain + terminal states
# --------------------------------------------------------------------------- #


def test_append_log_infers_level_and_persists(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    manager._append_log(job, "Traceback (most recent call last): boom")
    manager._append_log(job, "be warned", level="warning")
    assert job.log_records[0]["level"] == "error"
    assert job.log_records[1]["level"] == "warning"
    assert job.logs[0] == "Traceback (most recent call last): boom"
    text = manager._text_path(job).read_text(encoding="utf-8")
    assert "[ERROR]" in text
    assert "[WARNING]" in text
    jsonl_records = [json.loads(line) for line in manager._jsonl_path(job).read_text(encoding="utf-8").splitlines()]
    assert [r["record_type"] for r in jsonl_records] == ["log", "log"]
    assert jsonl_records[0]["level"] == "error"


def test_append_log_honors_explicit_timestamp(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    manager._append_log(job, "stamped", level="info", timestamp="2026-08-11T00:00:00Z")
    assert job.log_records[0]["timestamp"] == "2026-08-11T00:00:00Z"
    assert "2026-08-11T00:00:00Z" in manager._text_path(job).read_text(encoding="utf-8")


def test_append_event_masks_secrets_and_updates_progress(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    manager._append_event(job, {
        "event": "local_project_progress",
        "project_index": 1,
        "project_total": 1,
        "api_key": "sk-abcdefghijklmnopqrstuvwxyz123456",
    })
    assert len(job.events) == 1
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in json.dumps(list(job.events))
    assert job.progress_state["project_index"] == 1
    lines = manager._jsonl_path(job).read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["record_type"] == "event"


def test_drain_output_splits_events_from_plain_lines(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    event_line = serialize_event({
        "event": "local_project_progress", "project_index": 1, "project_total": 2,
        "phase": "inventory", "phase_status": "passed",
    })
    fake_process = types.SimpleNamespace(stdout=io.StringIO(event_line + "\nplain log line\n"))
    manager._drain_output(job, fake_process)
    assert len(job.events) == 1
    assert job.events[0]["event"] == "local_project_progress"
    assert list(job.logs) == ["plain log line"]
    assert job.progress_state["phase_status"] == "passed"
    # Snapshot was persisted per line.
    assert json.loads(manager._snapshot_path(job).read_text(encoding="utf-8"))["step"] is None


def test_drain_output_throttles_snapshot_writes(tmp_path: Path, monkeypatch) -> None:
    """A chatty child must not rewrite the full snapshot for every line."""
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    writes: list[float] = []
    real_persist = manager._persist_snapshot

    def spy(target_job):
        writes.append(time.monotonic())
        return real_persist(target_job)

    monkeypatch.setattr(manager, "_persist_snapshot", spy)
    fake_process = types.SimpleNamespace(stdout=io.StringIO("\n".join(f"line {i}" for i in range(50)) + "\n"))
    manager._drain_output(job, fake_process)
    assert list(job.logs) == [f"line {i}" for i in range(50)]
    # 50 rapid lines: zero throttled persists inside the loop, then exactly
    # one final snapshot so restart recovery still sees the finished state.
    # The count is coupled to _SNAPSHOT_THROTTLE_SECONDS — StringIO lines
    # process in microseconds, far under the 0.5 s window, so it is stable.
    assert len(writes) == 1


def test_finalize_completed_and_failed(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path, status="running")
    manager._finalize(job, 0)
    assert job.status == "completed"
    assert job.return_code == 0
    assert job.progress_state["job_status"] == "completed"
    assert job.progress_state["overall_progress"] == 1.0
    finished = job.events[-1]
    assert finished["event"] == "job_finished"
    assert finished["level"] == "info"
    assert finished["return_code"] == 0
    assert finished["overall_progress"] == 1.0

    failed = _make_job(tmp_path, status="running")
    manager._finalize(failed, 1)
    assert failed.status == "failed"
    assert failed.events[-1]["level"] == "error"
    assert failed.events[-1]["message"] == "The job did not finish successfully."


def test_finalize_keeps_existing_verdict_and_stopping_state(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)

    # The stall watchdog already ended the job -> child exit must not win.
    watchdog = _make_job(tmp_path, status="failed", return_code=-3)
    manager._finalize(watchdog, 0)
    assert watchdog.status == "failed"
    assert watchdog.return_code == -3

    stopping = _make_job(tmp_path, status="stopping")
    manager._finalize(stopping, -15)
    assert stopping.status == "stopped"
    assert stopping.return_code == -15


def test_fail_records_studio_side_error(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path, status="running")
    manager._fail(job, RuntimeError("boom"))
    assert job.status == "failed"
    assert job.return_code == -1
    assert job.progress_state["failure_reason"] == "RuntimeError: boom"
    assert any("Studio job error" in record["text"] for record in job.log_records)


# --------------------------------------------------------------------------- #
# stall watchdog + process spawning
# --------------------------------------------------------------------------- #


def test_stall_watchdog_zero_disables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GODOT_CODER_JOB_STALL_TIMEOUT_SECONDS", "0")
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path, status="running")
    assert manager._start_stall_watchdog(job) is None  # no thread started


def test_stall_watchdog_malformed_env_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GODOT_CODER_JOB_STALL_TIMEOUT_SECONDS", "not-a-number")
    # Let the watchdog thread exit immediately instead of sleeping 10 s.
    monkeypatch.setattr(jobs_module.time, "sleep", lambda _: None)
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path, status="running")
    manager._start_stall_watchdog(job)
    time.sleep(0.05)  # give the daemon thread a moment to check and return
    assert job.status == "running"  # untouched: no process, no stall


def test_spawn_process_sets_env_and_creation_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class _FakePopen:
        def __init__(self, command, **kwargs) -> None:
            captured["command"] = command
            captured["kwargs"] = kwargs
            self.pid = 4242

    # Patching subprocess.Popen touches the shared module, but this test never
    # spawns anything itself and monkeypatch restores it afterwards.
    monkeypatch.setattr(jobs_module.subprocess, "Popen", _FakePopen)
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path, extra_env={"GODOT_CODER_TOGGLE": "yes"})
    process = manager._spawn_process(job)
    assert process.pid == 4242
    assert captured["command"] == job.command
    kwargs = captured["kwargs"]  # type: ignore[assignment]
    env = kwargs["env"]
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["GODOT_CODER_JOB_ID"] == job.id
    assert env["GODOT_CODER_TOGGLE"] == "yes"
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["text"] is True
    assert kwargs["stdout"] == jobs_module.subprocess.PIPE
    assert kwargs["stderr"] == jobs_module.subprocess.STDOUT
    if os.name == "nt":
        assert kwargs["creationflags"] & getattr(jobs_module.subprocess, "CREATE_NO_WINDOW", 0)


def test_stop_writes_stop_file_and_child_sees_it(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    script = (
        "import os, time\n"
        "print(os.environ.get('GODOT_CODER_STOP_FILE', ''), flush=True)\n"
        "time.sleep(30)\n"
    )
    started = manager.start("test-kind", ["-c", script])
    job_id = started["id"]
    stop_path = tmp_path / "reports" / "studio_jobs" / f"{job_id}.stop"
    # The stop file exists only after stop() asks for a graceful shutdown.
    assert not stop_path.exists()
    # stop() returns early while the child process is not spawned yet, so
    # wait for the run to be live first (same pattern as the busy test).
    deadline = time.time() + 8
    while time.time() < deadline:
        snapshot = manager.current()
        if snapshot and snapshot["status"] == "running":
            break
        time.sleep(0.03)
    manager.stop()
    assert stop_path.exists()
    assert stop_path.read_text(encoding="utf-8") == "stop"
    snapshot = _wait_for_terminal(manager)
    assert snapshot["status"] == "stopped"
    logs = "\n".join(snapshot["logs"])
    assert str(stop_path) in logs  # the child received the path via the env


def test_interrupted_event_updates_progress_state(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = _make_job(tmp_path)
    manager._append_event(job, {
        "schema": "godot-coder-progress-event",
        "schema_version": 1,
        "event": "interrupted",
        "job_id": job.id,
        "interrupted_step": 42,
        "interrupted_checkpoint": "checkpoints/v06/step_00000042.pt",
        "timestamp": "2026-01-01T00:00:00Z",
    })
    state = job.snapshot()["progress_state"]
    assert state["interrupted_step"] == 42
    assert state["interrupted_checkpoint"] == "checkpoints/v06/step_00000042.pt"


def test_start_accepts_initial_progress(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    started = manager.start(
        "test-kind",
        ["-c", "print('hi', flush=True)"],
        initial_progress={"resume_step": 5, "resumed_from": "checkpoints/v06/latest.pt"},
    )
    assert started["progress_state"]["resume_step"] == 5
    assert started["progress_state"]["resumed_from"] == "checkpoints/v06/latest.pt"
    _wait_for_terminal(manager)
