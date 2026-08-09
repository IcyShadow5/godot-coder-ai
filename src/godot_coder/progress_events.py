from __future__ import annotations

import json
import os
import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

EVENT_PREFIX = "GCAI_EVENT "
EVENT_SCHEMA_NAME = "godot-coder-progress-event"
EVENT_SCHEMA_VERSION = 1

PHASE_LABELS: dict[str, str] = {
    "input_detection": "Detect ZIP or folder",
    "secure_extract": "Extract safely",
    "project_detection": "Detect project.godot",
    "inventory": "Inventory files",
    "cache_exclusion": "Exclude cache and import files",
    "addon_classification": "Classify add-ons",
    "secret_scan": "Secret scan",
    "file_size_check": "File-size check",
    "static_analysis": "Static GDScript check",
    "deduplication": "Deduplicate source",
    "corpus_admission": "Adopt cleaned working copy",
    "godot_validation": "Godot project import and parser check",
    "quarantine_decision": "Quarantine decision",
    "registry_update": "Update corpus registry",
    "report_writing": "Write final report",
    "corpus_validation": "Project-based corpus validation",
    "remote_link_validation": "Check remote link safely",
    "remote_download": "Download source to this PC",
}

_LEVELS = {"info", "warning", "error"}
_STATUS_VALUES = {
    "waiting",
    "running",
    "passed",
    "passed_with_warnings",
    "failed",
    "quarantined",
    "disabled",
    "skipped",
    "completed",
    "stopped",
}

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.DOTALL),
    re.compile(
        r"(?i)(\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|password)\b\s*[:=]\s*)"
        r"([\"']?)[^\s,;\"']{8,}\2"
    ),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[A-Za-z0-9._~+\-/=]{8,}"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{22,})\b"),
    re.compile(r"\beyJ[A-Za-z0-9_=-]{8,}\.[A-Za-z0-9_=-]{8,}(?:\.[A-Za-z0-9_=-]{8,})?"),
    re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^:\s/@]+:)[^@\s/]+@"),
)

_NUMERIC_FIELDS = {
    "project_index",
    "project_total",
    "file_index",
    "file_total",
    "passed",
    "warnings",
    "failed",
    "quarantined",
    "addon_files",
    "generated_files",
    "remaining_files",
    "scripts_found",
    "trainable_scripts",
    "accepted",
    "next_project_scripts",
    "elapsed_seconds",
    "estimated_remaining_seconds",
    "estimated_remaining_min_seconds",
    "estimated_remaining_max_seconds",
    "return_code",
    "bytes_received",
    "bytes_total",
}


def utc_timestamp(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(epoch if epoch is not None else time.time(), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def mask_secrets(value: Any) -> Any:
    """Recursively mask likely credentials without exposing the detected value."""
    if isinstance(value, str):
        masked = value
        for pattern in _SECRET_PATTERNS:
            if pattern.groups:
                masked = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", masked)
            else:
                masked = pattern.sub("[REDACTED]", masked)
        return masked
    if isinstance(value, Mapping):
        return {str(key): mask_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [mask_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(mask_secrets(item) for item in value)
    return value


def _non_negative_number(value: Any, *, integer: bool = True) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number != number:
        return None
    return int(number) if integer else number


def normalize_event(payload: Mapping[str, Any], *, job_id: str | None = None, now: float | None = None) -> dict[str, Any]:
    """Normalize current and future event payloads while tolerating missing optional fields."""
    raw = dict(payload)
    version = _non_negative_number(raw.get("schema_version")) or EVENT_SCHEMA_VERSION
    event_name = str(raw.get("event") or "progress")[:80]
    level = str(raw.get("level") or "info").lower()
    if level not in _LEVELS:
        level = "info"
    normalized: dict[str, Any] = {
        "schema": str(raw.get("schema") or EVENT_SCHEMA_NAME),
        "schema_version": version,
        "event": event_name,
        "timestamp": str(raw.get("timestamp") or utc_timestamp(now)),
        "job_id": str(raw.get("job_id") or job_id or "")[:128] or None,
        "level": level,
    }
    for key, value in raw.items():
        if key in normalized:
            continue
        if key in _NUMERIC_FIELDS:
            integer = key not in {
                "elapsed_seconds",
                "estimated_remaining_seconds",
                "estimated_remaining_min_seconds",
                "estimated_remaining_max_seconds",
            }
            normalized[key] = _non_negative_number(value, integer=integer)
        elif key in {"overall_progress", "project_progress"}:
            number = _non_negative_number(value, integer=False)
            normalized[key] = min(1.0, number) if number is not None else None
        elif key in {"project_status", "phase_status", "job_status"}:
            text = str(value or "").lower()
            normalized[key] = text if text in _STATUS_VALUES else (text or None)
        else:
            normalized[key] = value
    return mask_secrets(normalized)


def serialize_event(payload: Mapping[str, Any], *, job_id: str | None = None) -> str:
    return EVENT_PREFIX + json.dumps(normalize_event(payload, job_id=job_id), ensure_ascii=False, sort_keys=True)


def parse_event_line(line: str, *, job_id: str | None = None) -> dict[str, Any] | None:
    if not line.startswith(EVENT_PREFIX):
        return None
    try:
        payload = json.loads(line[len(EVENT_PREFIX):])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return normalize_event(payload, job_id=job_id)


def estimate_remaining_seconds(
    *,
    elapsed_seconds: float,
    completed_units: int,
    total_units: int,
    minimum_samples: int = 3,
) -> float | None:
    if elapsed_seconds <= 0 or completed_units < minimum_samples or total_units <= completed_units:
        return None
    return max(0.0, (elapsed_seconds / completed_units) * (total_units - completed_units))


@dataclass
class EtaEstimator:
    """Low-precision estimator based on observed file durations and completed projects."""

    started_at: float = field(default_factory=time.monotonic)
    file_samples: list[float] = field(default_factory=list)
    project_samples: list[float] = field(default_factory=list)
    _last_file_at: float | None = None
    _last_project_at: float | None = None
    _last_estimate: dict[str, float | None] | None = None

    def observe_file(self, now: float | None = None) -> None:
        current = now if now is not None else time.monotonic()
        previous = self._last_file_at or self.started_at
        duration = max(0.0001, current - previous)
        self.file_samples.append(duration)
        self.file_samples = self.file_samples[-200:]
        self._last_file_at = current

    def observe_project(self, now: float | None = None) -> None:
        current = now if now is not None else time.monotonic()
        previous = self._last_project_at or self.started_at
        duration = max(0.0001, current - previous)
        self.project_samples.append(duration)
        self.project_samples = self.project_samples[-50:]
        self._last_project_at = current

    def estimate(
        self,
        *,
        remaining_files: int,
        remaining_projects: int,
    ) -> dict[str, float | None]:
        estimate: float | None = None
        samples: list[float] = []
        if len(self.file_samples) >= 3 and remaining_files > 0:
            file_mean = statistics.fmean(self.file_samples)
            estimate = file_mean * remaining_files
            samples.extend(self.file_samples)
        if self.project_samples and remaining_projects > 0:
            project_mean = statistics.fmean(self.project_samples)
            project_estimate = project_mean * remaining_projects
            estimate = max(estimate or 0.0, project_estimate)
            samples.extend(self.project_samples)
        if estimate is None:
            # Preserve the last known estimate so the UI doesn't flip to
            # "calculating" between projects when remaining_files hits zero.
            if self._last_estimate is not None:
                return self._last_estimate
            return {
                "estimated_remaining_seconds": None,
                "estimated_remaining_min_seconds": None,
                "estimated_remaining_max_seconds": None,
            }
        spread = 0.2
        if len(samples) >= 4:
            mean = statistics.fmean(samples)
            if mean > 0:
                spread = min(0.75, max(0.15, statistics.pstdev(samples) / mean))
        result = {
            "estimated_remaining_seconds": round(estimate, 1),
            "estimated_remaining_min_seconds": round(max(0.0, estimate * (1.0 - spread)), 1),
            "estimated_remaining_max_seconds": round(estimate * (1.0 + spread), 1),
        }
        self._last_estimate = result
        return result


class ProgressEmitter:
    def __init__(
        self,
        *,
        job_id: str | None = None,
        sink: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.job_id = job_id or os.environ.get("GODOT_CODER_JOB_ID")
        self.sink = sink or (lambda line: print(line, flush=True))
        self.clock = clock
        self.started_at = clock()

    def emit(self, event: str, /, **fields: Any) -> dict[str, Any]:
        fields.setdefault("elapsed_seconds", max(0.0, self.clock() - self.started_at))
        fields["event"] = event
        normalized = normalize_event(fields, job_id=self.job_id)
        self.sink(EVENT_PREFIX + json.dumps(normalized, ensure_ascii=False, sort_keys=True))
        return normalized
