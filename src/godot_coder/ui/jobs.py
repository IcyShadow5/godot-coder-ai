from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..process_control import terminate_process_tree
from ..progress_events import EVENT_SCHEMA_VERSION, mask_secrets, parse_event_line, utc_timestamp

_STEP_PATTERN = re.compile(r"(?:^|\s)step=(\d+)")
_VALIDATION_PATTERN = re.compile(r"validation step=(\d+)")
_CURRICULUM_PATTERN = re.compile(r"^\[(\d+)\]")
_BENCHMARK_PATTERN = re.compile(r"benchmark=(\d+)/")
_CORPUS_SOURCE_PATTERN = re.compile(r"source=(\d+)/(\d+)")
_CORPUS_VALIDATE_PATTERN = re.compile(r"validate=(\d+)/(\d+)")
_PROFILE_PROBE_PATTERN = re.compile(r"profile_probe=(\d+)/(\d+)")
_AUTOTUNE_PATTERN = re.compile(r"autotune=(\d+)/(\d+)")
_LOCAL_IMPORT_PATTERN = re.compile(r"local_import=(\d+)/(\d+)")
_LOCAL_PROJECT_PATTERN = re.compile(
    r"local_project=(?P<name>.+?)\s+scripts=(?P<scripts>\d+)\s+"
    r"validation=(?P<validation>\w+)\s+enabled=(?P<enabled>True|False)"
)
_ACTIVE_STATUSES = {"starting", "running", "stopping"}
_FINAL_STATUSES = {"completed", "failed", "stopped"}


def _level_from_text(text: str) -> str:
    if _CORPUS_VALIDATE_PATTERN.search(text):
        return "info"
    lowered = text.lower()
    if "traceback" in lowered or "error" in lowered or "failed" in lowered or "exception" in lowered:
        return "error"
    if "warning" in lowered or "warn" in lowered or "quarant" in lowered:
        return "warning"
    return "info"


def _project_sort_key(project: dict[str, Any]) -> tuple[int, str]:
    index = project.get("project_index")
    return (int(index) if isinstance(index, int) else 10**9, str(project.get("project_name") or ""))


@dataclass
class Job:
    id: str
    kind: str
    command: list[str]
    cwd: str
    # Extra env vars for the child process (Studio import toggles).
    # Deliberately excluded from snapshots — job state should never carry
    # env-like values.
    extra_env: dict[str, str] = field(default_factory=dict, repr=False)
    status: str = "starting"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    return_code: int | None = None
    pid: int | None = None
    step: int | None = None
    max_steps: int | None = None
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=5000))
    log_records: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=5000))
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=5000))
    progress_state: dict[str, Any] = field(default_factory=dict)
    last_successful_step: dict[str, Any] | None = None
    process: subprocess.Popen[str] | None = field(default=None, repr=False)

    def elapsed_seconds(self, now: float | None = None) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at if self.finished_at is not None else (now if now is not None else time.time())
        return max(0.0, end - self.started_at)

    def snapshot(self) -> dict[str, object]:
        progress = self.progress_state.get("overall_progress")
        if progress is None and self.step is not None and self.max_steps:
            progress = min(1.0, self.step / self.max_steps)
        state = dict(self.progress_state)
        state["projects"] = sorted(
            [dict(project) for project in state.get("projects", []) if isinstance(project, dict)],
            key=_project_sort_key,
        )
        state.setdefault("elapsed_seconds", round(self.elapsed_seconds(), 1))
        return {
            "id": self.id,
            "kind": self.kind,
            "command": mask_secrets(self.command),
            "cwd": self.cwd,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "return_code": self.return_code,
            "pid": self.pid,
            "step": self.step,
            "max_steps": self.max_steps,
            "progress": progress,
            "elapsed_seconds": round(self.elapsed_seconds(), 1),
            "logs": list(self.logs),
            "log_records": list(self.log_records),
            "events": list(self.events),
            "progress_state": state,
            "last_successful_step": self.last_successful_step,
            "event_schema_version": EVENT_SCHEMA_VERSION,
        }

    @classmethod
    def from_snapshot(cls, payload: dict[str, Any]) -> "Job":
        job = cls(
            id=str(payload.get("id") or uuid.uuid4().hex[:12]),
            kind=str(payload.get("kind") or "unknown"),
            command=[str(item) for item in payload.get("command") or []],
            cwd=str(payload.get("cwd") or ""),
            status=str(payload.get("status") or "failed"),
            created_at=float(payload.get("created_at") or time.time()),
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
            return_code=payload.get("return_code"),
            pid=payload.get("pid"),
            step=payload.get("step"),
            max_steps=payload.get("max_steps"),
        )
        job.logs.extend(str(line) for line in payload.get("logs") or [])
        job.log_records.extend(item for item in payload.get("log_records") or [] if isinstance(item, dict))
        job.events.extend(item for item in payload.get("events") or [] if isinstance(item, dict))
        job.progress_state = dict(payload.get("progress_state") or {})
        job.last_successful_step = payload.get("last_successful_step")
        return job


class JobManager:
    """Runs one long task at a time, persists progress, and keeps bounded API snapshots."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.state_dir = project_root / "reports" / "studio_jobs"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._current: Job | None = None
        self._history: deque[Job] = deque(maxlen=20)
        self._load_history()

    def _snapshot_path(self, job: Job | str) -> Path:
        job_id = job if isinstance(job, str) else job.id
        return self.state_dir / f"{job_id}.snapshot.json"

    def _text_path(self, job: Job | str) -> Path:
        job_id = job if isinstance(job, str) else job.id
        return self.state_dir / f"{job_id}.log.txt"

    def _jsonl_path(self, job: Job | str) -> Path:
        job_id = job if isinstance(job, str) else job.id
        return self.state_dir / f"{job_id}.log.jsonl"

    def _load_history(self) -> None:
        snapshots: list[tuple[float, Job]] = []
        for path in self.state_dir.glob("*.snapshot.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    job = Job.from_snapshot(payload)
                    snapshots.append((job.created_at, job))
            except (OSError, ValueError, TypeError):
                continue
        snapshots.sort(key=lambda item: item[0])
        for _, job in snapshots[-20:]:
            if job.status in _ACTIVE_STATUSES:
                job.status = "failed"
                job.finished_at = time.time()
                job.return_code = -2
                job.progress_state["job_status"] = "failed"
                job.progress_state["failure_reason"] = "Studio wurde während des Jobs neu gestartet."
                self._append_log(job, "Studio-Neustart erkannt; der unvollständige Job wurde als fehlgeschlagen markiert.", level="error")
                self._persist_snapshot(job)
            self._history.append(job)
        if self._history:
            self._current = self._history[-1]
        # Clean up on-disk files for jobs beyond the last 20.
        # Wrapped in try/except so a locked log file (antivirus, etc.)
        # doesn't crash Studio startup.
        try:
            active_ids = {job.id for job in self._history}
            for path in self.state_dir.glob("*.snapshot.json"):
                job_id = path.stem.replace(".snapshot", "")
                if job_id in active_ids:
                    continue
                for suffix in (".snapshot.json", ".log.txt", ".log.jsonl"):
                    stale = self.state_dir / f"{job_id}{suffix}"
                    stale.unlink(missing_ok=True)
        except OSError:
            pass

    def current(self) -> dict[str, object] | None:
        with self._lock:
            return self._current.snapshot() if self._current else None

    def history(self) -> list[dict[str, object]]:
        with self._lock:
            return [job.snapshot() for job in reversed(self._history)]

    def is_busy(self) -> bool:
        with self._lock:
            return self._current is not None and self._current.status in _ACTIVE_STATUSES

    def start(
        self,
        kind: str,
        args: Sequence[str],
        *,
        max_steps: int | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        with self._lock:
            if self.is_busy():
                raise RuntimeError("another studio task is already running")
            command = [sys.executable, "-u", *args]
            job = Job(
                id=uuid.uuid4().hex[:12],
                kind=kind,
                command=command,
                cwd=str(self.project_root),
                max_steps=max_steps,
                extra_env=dict(extra_env or {}),
                progress_state={
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "job_status": "starting",
                    "projects": [],
                },
            )
            self._current = job
            self._history.append(job)
            self._persist_snapshot(job)
            thread = threading.Thread(target=self._run, args=(job,), daemon=True)
            thread.start()
            return job.snapshot()

    def stop(self) -> dict[str, object] | None:
        with self._lock:
            job = self._current
            if job is None or job.process is None or job.process.poll() is not None:
                return job.snapshot() if job else None
            job.status = "stopping"
            job.progress_state["job_status"] = "stopping"
            self._persist_snapshot(job)
            process = job.process
        self._terminate_tree(process)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self._kill_tree(process)
        return job.snapshot()

    def export_path(self, job_id: str, output_format: str) -> Path:
        with self._lock:
            if not re.fullmatch(r"[A-Za-z0-9_-]{4,128}", job_id):
                raise ValueError("invalid job id")
            known = any(job.id == job_id for job in self._history)
            if not known:
                raise FileNotFoundError(f"unknown job: {job_id}")
            if output_format == "text":
                path = self._text_path(job_id)
            elif output_format == "jsonl":
                path = self._jsonl_path(job_id)
            else:
                raise ValueError("format must be text or jsonl")
            if not path.exists():
                raise FileNotFoundError(f"log export not found: {job_id}")
            return path

    @staticmethod
    def _terminate_tree(process: subprocess.Popen[str]) -> None:
        if not terminate_process_tree(process.pid, force=False, wait_seconds=3.0):
            try:
                process.terminate()
            except OSError:
                pass

    @staticmethod
    def _kill_tree(process: subprocess.Popen[str]) -> None:
        if not terminate_process_tree(process.pid, force=True, wait_seconds=5.0):
            try:
                process.kill()
            except OSError:
                pass

    def _append_jsonl(self, job: Job, record: dict[str, Any]) -> None:
        path = self._jsonl_path(job)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(mask_secrets(record), ensure_ascii=False, sort_keys=True) + "\n")

    def _append_log(self, job: Job, text: str, *, level: str | None = None, timestamp: str | None = None) -> None:
        masked = str(mask_secrets(text))
        record = {
            "record_type": "log",
            "timestamp": timestamp or utc_timestamp(),
            "level": level or _level_from_text(masked),
            "text": masked,
        }
        job.logs.append(masked)
        job.log_records.append(record)
        with self._text_path(job).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"[{record['timestamp']}] [{record['level'].upper()}] {masked}\n")
        self._append_jsonl(job, record)
        self._update_legacy_progress(job, masked)

    def _append_event(self, job: Job, event: dict[str, Any]) -> None:
        safe_event = mask_secrets(event)
        job.events.append(safe_event)
        self._append_jsonl(job, {"record_type": "event", **safe_event})
        self._update_progress(job, safe_event)

    def _update_legacy_progress(self, job: Job, line: str) -> None:
        match = (
            _STEP_PATTERN.search(line)
            or _VALIDATION_PATTERN.search(line)
            or _CURRICULUM_PATTERN.search(line)
            or _BENCHMARK_PATTERN.search(line)
            or _CORPUS_SOURCE_PATTERN.search(line)
            or _CORPUS_VALIDATE_PATTERN.search(line)
            or _PROFILE_PROBE_PATTERN.search(line)
            or _AUTOTUNE_PATTERN.search(line)
            or _LOCAL_IMPORT_PATTERN.search(line)
        )
        if match:
            job.step = int(match.group(1))
        local_import = _LOCAL_IMPORT_PATTERN.search(line)
        if local_import and not job.events:
            index, total = (int(local_import.group(1)), int(local_import.group(2)))
            job.progress_state.update({
                "project_index": index,
                "project_total": total,
                "overall_progress": min(1.0, index / max(1, total)),
                "phase": "input_detection",
                "legacy_text_progress": True,
            })
        local_project = _LOCAL_PROJECT_PATTERN.search(line)
        if local_project and not job.events:
            name = local_project.group("name").strip("'\"")
            scripts = int(local_project.group("scripts"))
            validation = local_project.group("validation")
            enabled = local_project.group("enabled") == "True"
            projects = list(job.progress_state.get("projects") or [])
            project = {
                "project_name": name,
                "scripts_found": scripts,
                "file_total": scripts,
                "project_status": "passed" if enabled else "quarantined",
                "validation_status": validation,
                "enabled_for_training": enabled,
                "legacy_text_progress": True,
            }
            projects.append(project)
            job.progress_state["projects"] = projects

    def _update_progress(self, job: Job, event: dict[str, Any]) -> None:
        state = job.progress_state
        state["schema_version"] = event.get("schema_version", EVENT_SCHEMA_VERSION)
        state["last_event"] = event.get("event")
        state["last_event_timestamp"] = event.get("timestamp")
        if isinstance(event.get("projects"), list):
            state["projects"] = [dict(item) for item in event["projects"] if isinstance(item, dict)]
        for key in (
            "project_index", "project_total", "project_name", "phase", "phase_label", "phase_status",
            "current_file", "file_index", "file_total", "passed", "warnings", "failed", "quarantined",
            "addon_files", "generated_files", "remaining_files", "scripts_found", "trainable_scripts",
            "next_project", "next_project_scripts", "next_phase", "elapsed_seconds",
            "estimated_remaining_seconds", "estimated_remaining_min_seconds", "estimated_remaining_max_seconds",
            "overall_progress", "project_progress", "message", "job_status", "failure_reason",
            "bytes_received", "bytes_total", "source_name", "source_url", "eta_status",
            "validation_mode", "validation_fallback", "validation_infrastructure_failure",
            "parser_checked_files", "parser_failed_files", "phase_elapsed_seconds", "accepted",
        ):
            if key in event and event[key] is not None:
                state[key] = event[key]
        if event.get("project_total") and event.get("project_index") and event.get("overall_progress") is None:
            state["overall_progress"] = min(1.0, float(event["project_index"]) / float(event["project_total"]))
        project_name = event.get("project_name")
        project_index = event.get("project_index")
        if project_name is not None or project_index is not None:
            projects = [dict(item) for item in state.get("projects", []) if isinstance(item, dict)]
            match_index = next(
                (
                    index for index, item in enumerate(projects)
                    if (project_index is not None and item.get("project_index") == project_index)
                    or (project_name is not None and item.get("project_name") == project_name)
                ),
                None,
            )
            project = projects[match_index] if match_index is not None else {}
            for key in (
                "project_index", "project_total", "project_name", "project_status", "phase", "phase_label",
                "phase_status", "current_file", "file_index", "file_total", "passed", "warnings", "failed",
                "quarantined", "addon_files", "generated_files", "remaining_files", "scripts_found",
                "trainable_scripts", "validation_status", "validation_mode", "validation_fallback",
                "validation_infrastructure_failure", "parser_checked_files", "parser_failed_files",
                "enabled_for_training", "message",
            ):
                if key in event and event[key] is not None:
                    project[key] = event[key]
            phase = event.get("phase")
            if phase:
                phases = [dict(item) for item in project.get("phases", []) if isinstance(item, dict)]
                phase_index = next((index for index, item in enumerate(phases) if item.get("phase") == phase), None)
                phase_entry = phases[phase_index] if phase_index is not None else {"phase": phase}
                phase_entry.update({
                    key: event[key] for key in ("phase", "phase_label", "phase_status", "timestamp", "message")
                    if key in event and event[key] is not None
                })
                if phase_index is None:
                    phases.append(phase_entry)
                else:
                    phases[phase_index] = phase_entry
                project["phases"] = phases
            if match_index is None:
                projects.append(project)
            else:
                projects[match_index] = project
            state["projects"] = projects
        if event.get("phase_status") in {"passed", "completed", "passed_with_warnings"}:
            job.last_successful_step = {
                "timestamp": event.get("timestamp"),
                "project_name": event.get("project_name"),
                "phase": event.get("phase"),
                "phase_label": event.get("phase_label"),
                "current_file": event.get("current_file"),
                "message": event.get("message"),
            }

    def _persist_snapshot(self, job: Job) -> None:
        path = self._snapshot_path(job)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(job.snapshot(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)

    def _run(self, job: Job) -> None:
        creation_flags = 0
        if os.name == "nt":
            creation_flags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        try:
            child_env = os.environ.copy()
            # Studio import toggles arrive via job.extra_env — no shell needed.
            child_env.update(job.extra_env)
            child_env["PYTHONUTF8"] = "1"
            child_env["PYTHONIOENCODING"] = "utf-8"
            child_env["GODOT_CODER_JOB_ID"] = job.id
            process = subprocess.Popen(
                job.command,
                cwd=job.cwd,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creation_flags,
                start_new_session=os.name != "nt",
            )
            with self._lock:
                job.process = process
                job.pid = process.pid
                job.status = "running"
                job.started_at = time.time()
                job.progress_state["job_status"] = "running"
                self._persist_snapshot(job)
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = raw_line.rstrip("\r\n")
                with self._lock:
                    event = parse_event_line(line, job_id=job.id)
                    if event is not None:
                        self._append_event(job, event)
                    else:
                        self._append_log(job, line)
                    self._persist_snapshot(job)
            return_code = process.wait()
            with self._lock:
                job.return_code = return_code
                job.finished_at = time.time()
                if job.status == "stopping":
                    job.status = "stopped"
                else:
                    job.status = "completed" if return_code == 0 else "failed"
                job.progress_state["job_status"] = job.status
                if job.status == "completed":
                    job.progress_state["overall_progress"] = 1.0
                self._append_event(job, {
                    "schema": "godot-coder-progress-event",
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "event": "job_finished",
                    "timestamp": utc_timestamp(),
                    "job_id": job.id,
                    "level": "info" if job.status == "completed" else "error",
                    "job_status": job.status,
                    "return_code": max(0, return_code) if return_code >= 0 else None,
                    "elapsed_seconds": job.elapsed_seconds(),
                    "overall_progress": 1.0 if job.status == "completed" else job.progress_state.get("overall_progress"),
                    "message": "Job abgeschlossen." if job.status == "completed" else "Job wurde nicht erfolgreich abgeschlossen.",
                })
                self._persist_snapshot(job)
        except Exception as exc:  # pragma: no cover - defensive process boundary
            with self._lock:
                self._append_log(job, f"Studio job error: {type(exc).__name__}: {exc}", level="error")
                job.return_code = -1
                job.finished_at = time.time()
                job.status = "failed"
                job.progress_state["job_status"] = "failed"
                job.progress_state["failure_reason"] = f"{type(exc).__name__}: {mask_secrets(str(exc))}"
                self._persist_snapshot(job)
