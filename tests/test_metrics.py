"""Tests for the metrics collector — JSONL persistence, summaries, event enum, end to end."""

import json
from pathlib import Path

from godot_coder.metrics import MetricEvent, MetricRecord, MetricsCollector


def test_event_enum_covers_pipeline_outcomes() -> None:
    names = {e.name for e in MetricEvent}
    expected = {
        "PARSE_SUCCESS", "PARSE_ERROR", "RUNTIME_SUCCESS", "RUNTIME_ERROR",
        "TOOL_TIMEOUT", "TOOL_ERROR", "ENVIRONMENT_ERROR", "INFRASTRUCTURE_ERROR",
        "TOKEN_USAGE", "GENERATION_COMPLETE", "GENERATION_ERROR", "RETRY", "ABORT",
    }
    assert expected <= names


def test_metric_record_to_dict_omits_none_fields() -> None:
    record = MetricRecord(event="parse_error", step=3, tokens=42)
    data = record.to_dict()
    assert data["event"] == "parse_error"
    assert data["step"] == 3
    assert data["tokens"] == 42
    assert data["time"] == record.timestamp
    assert "duration_seconds" not in data
    assert "error" not in data
    assert "details" not in data


def test_metric_record_to_dict_includes_error_fields() -> None:
    record = MetricRecord(
        event="runtime_error",
        error="boom",
        error_kind="value_error",
        attempt=2,
        max_attempts=3,
        details={"a": 1},
    )
    data = record.to_dict()
    assert data["error"] == "boom"
    assert data["error_kind"] == "value_error"
    assert data["attempt"] == 2
    assert data["max_attempts"] == 3
    assert data["details"] == {"a": 1}


def test_collector_records_in_memory() -> None:
    collector = MetricsCollector()
    record = collector.record(MetricEvent.TOKEN_USAGE, tokens=128)
    assert isinstance(record, MetricRecord)
    assert collector.count(MetricEvent.TOKEN_USAGE) == 1
    assert collector.count(MetricEvent.PARSE_ERROR) == 0


def test_collector_persists_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "metrics.jsonl"
    collector = MetricsCollector(path)
    collector.record(MetricEvent.PARSE_SUCCESS, step=1)
    collector.record(MetricEvent.TOKEN_USAGE, tokens=256)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "parse_success"
    assert json.loads(lines[1])["tokens"] == 256


def test_collector_summary_empty() -> None:
    assert MetricsCollector().summary() == {"events": 0}


def test_collector_summary_totals() -> None:
    collector = MetricsCollector()
    collector.record(MetricEvent.TOKEN_USAGE, tokens=100)
    collector.record(MetricEvent.TOKEN_USAGE, tokens=50)
    collector.record(MetricEvent.PARSE_ERROR, error="x")
    summary = collector.summary()
    assert summary["events"] == 3
    assert summary["total_tokens"] == 150
    assert summary["errors"] == 1
    assert summary["by_kind"] == {"token_usage": 2, "parse_error": 1}


def test_record_uses_lowercase_event_name() -> None:
    collector = MetricsCollector()
    record = collector.record(MetricEvent.GENERATION_COMPLETE)
    assert record.event == "generation_complete"
