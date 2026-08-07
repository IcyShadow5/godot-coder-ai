from __future__ import annotations

import json
import time
from pathlib import Path

from godot_coder.progress_events import serialize_event
from godot_coder.ui.jobs import JobManager


def _wait_for_terminal(manager: JobManager, timeout: float = 8.0) -> dict[str, object]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = manager.current()
        if snapshot and snapshot["status"] in {"completed", "failed", "stopped"}:
            return snapshot
        time.sleep(0.03)
    raise AssertionError("job did not finish")


def test_structured_progress_multiple_projects_and_persistence(tmp_path: Path) -> None:
    events = [
        serialize_event({
            "event": "local_project_progress", "project_index": 1, "project_total": 2,
            "project_name": "First", "phase": "static_analysis", "phase_status": "passed",
            "file_index": 1, "file_total": 1, "passed": 1, "overall_progress": 0.5,
            "message": "First passed",
        }),
        serialize_event({
            "event": "local_project_progress", "project_index": 2, "project_total": 2,
            "project_name": "Second", "phase": "godot_validation", "phase_status": "failed",
            "file_index": 2, "file_total": 3, "warnings": 1, "failed": 1,
            "overall_progress": 0.8, "message": "Parser failed", "level": "error",
        }),
    ]
    script = "import sys\n" + "\n".join(f"print({line!r}, flush=True)" for line in events) + "\nsys.exit(1)\n"
    manager = JobManager(tmp_path)
    manager.start("local-source-import", ["-c", script], max_steps=2)
    snapshot = _wait_for_terminal(manager)
    assert snapshot["status"] == "failed"
    state = snapshot["progress_state"]
    assert state["project_index"] == 2
    assert len(state["projects"]) == 2
    assert snapshot["last_successful_step"]["project_name"] == "First"

    restored = JobManager(tmp_path).current()
    assert restored is not None
    assert restored["id"] == snapshot["id"]
    assert restored["status"] == "failed"
    assert restored["progress_state"]["project_name"] == "Second"


def test_completed_job_and_full_exports(tmp_path: Path) -> None:
    event = serialize_event({
        "event": "local_project_completed", "project_index": 1, "project_total": 1,
        "project_name": "Demo", "phase": "quarantine_decision", "phase_status": "passed",
        "project_status": "passed", "overall_progress": 1.0, "message": "Done",
    })
    manager = JobManager(tmp_path)
    started = manager.start("local-source-import", ["-c", f"print({event!r}); print('normal text log')"])
    snapshot = _wait_for_terminal(manager)
    assert snapshot["status"] == "completed"
    assert snapshot["progress"] == 1.0
    text_path = manager.export_path(started["id"], "text")
    jsonl_path = manager.export_path(started["id"], "jsonl")
    assert "normal text log" in text_path.read_text(encoding="utf-8")
    records = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert any(record.get("record_type") == "event" for record in records)
    assert any(record.get("record_type") == "log" for record in records)


def test_aborted_job_keeps_last_successful_step(tmp_path: Path) -> None:
    event = serialize_event({
        "event": "local_project_phase", "project_index": 1, "project_total": 1,
        "project_name": "Slow", "phase": "inventory", "phase_status": "completed",
        "message": "Inventory complete",
    })
    script = f"import time\nprint({event!r}, flush=True)\ntime.sleep(30)\n"
    manager = JobManager(tmp_path)
    manager.start("local-source-import", ["-c", script])
    deadline = time.time() + 4
    while time.time() < deadline:
        current = manager.current()
        if current and current["last_successful_step"]:
            break
        time.sleep(0.03)
    manager.stop()
    snapshot = _wait_for_terminal(manager)
    assert snapshot["status"] == "stopped"
    assert snapshot["last_successful_step"]["phase"] == "inventory"


def test_legacy_text_progress_remains_supported(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    manager.start("local-source-import", [
        "-c",
        "print('local_import=1/2 item=one phase=inspect'); "
        "print(\"local_project='Old Demo' scripts=3 validation=passed enabled=True\")",
    ], max_steps=2)
    snapshot = _wait_for_terminal(manager)
    assert snapshot["progress_state"]["legacy_text_progress"] is True
    assert snapshot["progress_state"]["projects"][0]["project_name"] == "Old Demo"
