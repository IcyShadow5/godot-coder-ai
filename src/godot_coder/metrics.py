"""Events I want to track during training, validation, and generation.

Every parse error, timeout, retry, and token count gets recorded as a
typed event. The JSONL output matches the training metrics format, so
anything that reads training logs picks these up automatically.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any


class MetricEvent(Enum):
    """What happened — parse success, timeout, retry, token count, etc."""

    # ---- Pipeline outcomes ----
    PARSE_SUCCESS = auto()
    PARSE_ERROR = auto()
    RUNTIME_SUCCESS = auto()
    RUNTIME_ERROR = auto()

    # ---- Tool / environment ----
    TOOL_TIMEOUT = auto()
    TOOL_ERROR = auto()
    ENVIRONMENT_ERROR = auto()
    INFRASTRUCTURE_ERROR = auto()

    # ---- Generation ----
    TOKEN_USAGE = auto()
    GENERATION_COMPLETE = auto()
    GENERATION_ERROR = auto()

    # ---- Process lifecycle ----
    RETRY = auto()
    ABORT = auto()


@dataclass
class MetricRecord:
    """One event, flattened for JSONL. All fields optional except event name."""

    event: str
    timestamp: float = field(default_factory=time.time)
    step: int | None = None
    duration_seconds: float | None = None
    tokens: int | None = None
    context_tokens: int | None = None
    attempt: int | None = None
    max_attempts: int | None = None
    error: str | None = None
    error_kind: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "event": self.event,
            "time": self.timestamp,
        }
        if self.step is not None:
            result["step"] = self.step
        if self.duration_seconds is not None:
            result["duration_seconds"] = self.duration_seconds
        if self.tokens is not None:
            result["tokens"] = self.tokens
        if self.context_tokens is not None:
            result["context_tokens"] = self.context_tokens
        if self.attempt is not None:
            result["attempt"] = self.attempt
        if self.max_attempts is not None:
            result["max_attempts"] = self.max_attempts
        if self.error:
            result["error"] = self.error
        if self.error_kind:
            result["error_kind"] = self.error_kind
        if self.details:
            result["details"] = self.details
        return result


class MetricsCollector:
    """Append-only JSONL. Every write flushes so a crash doesn't lose records.

    Wired into train.py (per-interval token counts, validation success/error)
    and services.py (validate_code parse results, generate token counts).
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.records: list[MetricRecord] = []

    def record(self, event: MetricEvent, /, **fields: Any) -> MetricRecord:
        record = MetricRecord(event=event.name.lower(), **fields)
        self.records.append(record)
        if self.path is not None:
            self._write(record)
        return record

    def _write(self, record: MetricRecord) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
            handle.flush()

    def count(self, event: MetricEvent) -> int:
        name = event.name.lower()
        return sum(1 for r in self.records if r.event == name)

    def summary(self) -> dict[str, Any]:
        if not self.records:
            return {"events": 0}
        kinds: dict[str, int] = {}
        total_tokens = 0
        errors = 0
        for r in self.records:
            kinds[r.event] = kinds.get(r.event, 0) + 1
            total_tokens += r.tokens or 0
            if r.error:
                errors += 1
        return {
            "events": len(self.records),
            "by_kind": kinds,
            "total_tokens": total_tokens,
            "errors": errors,
        }
