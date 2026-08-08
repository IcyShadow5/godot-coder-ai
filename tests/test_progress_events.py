from __future__ import annotations

import json

from godot_coder.progress_events import (
    EVENT_PREFIX,
    EtaEstimator,
    estimate_remaining_seconds,
    mask_secrets,
    normalize_event,
    parse_event_line,
    serialize_event,
)


def test_event_normalization_tolerates_missing_optional_fields() -> None:
    event = normalize_event({"event": "local_project_progress"}, job_id="job-1", now=0)
    assert event["schema_version"] == 1
    assert event["job_id"] == "job-1"
    assert event["level"] == "info"
    assert "project_name" not in event


def test_structured_event_serialization_round_trip_and_windows_path() -> None:
    line = serialize_event({
        "event": "local_project_progress",
        "project_name": "Windows Demo",
        "current_file": r"scripts\\very\\long\\player_controller.gd",
        "file_index": 2,
        "file_total": 4,
    }, job_id="abc123")
    assert line.startswith(EVENT_PREFIX)
    parsed = parse_event_line(line)
    assert parsed is not None
    assert parsed["job_id"] == "abc123"
    assert parsed["current_file"] == r"scripts\\very\\long\\player_controller.gd"
    assert json.loads(line[len(EVENT_PREFIX):])["schema_version"] == 1


def test_secret_masking_covers_tokens_assignments_and_nested_values() -> None:
    secret = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    payload = mask_secrets({
        "command": f"tool --token {secret}",
        "detail": "password=supersecretpassword123",
        "nested": [f"Authorization: Bearer {secret}"],
    })
    serialized = json.dumps(payload)
    assert secret not in serialized
    assert "supersecretpassword123" not in serialized
    assert "[REDACTED]" in serialized


def test_eta_requires_enough_data() -> None:
    assert estimate_remaining_seconds(elapsed_seconds=4, completed_units=2, total_units=10) is None
    assert estimate_remaining_seconds(elapsed_seconds=6, completed_units=3, total_units=9) == 12


def test_eta_estimator_returns_range_after_samples() -> None:
    clock = 0.0
    estimator = EtaEstimator(started_at=clock)
    for clock in (1.0, 2.2, 3.1, 4.5):
        estimator.observe_file(clock)
    result = estimator.estimate(remaining_files=6, remaining_projects=0)
    assert result["estimated_remaining_seconds"] is not None
    assert result["estimated_remaining_min_seconds"] <= result["estimated_remaining_seconds"]
    assert result["estimated_remaining_max_seconds"] >= result["estimated_remaining_seconds"]


def test_eta_preserves_last_estimate_on_zero_remaining() -> None:
    """The ETA must not flip to None / "calculating" between projects when
    remaining_files temporarily drops to zero.  The cached last estimate
    carries through so the UI stays stable."""
    estimator = EtaEstimator(started_at=0.0)
    estimator.observe_file(1.0)
    estimator.observe_file(2.0)
    estimator.observe_file(3.0)
    estimator.observe_file(4.0)

    # Normal call produces a valid estimate …
    result = estimator.estimate(remaining_files=10, remaining_projects=2)
    assert result["estimated_remaining_seconds"] is not None
    cached = result["estimated_remaining_seconds"]

    # … and a follow-up call with zero remaining returns the cache.
    result2 = estimator.estimate(remaining_files=0, remaining_projects=0)
    assert result2["estimated_remaining_seconds"] is not None
    assert result2["estimated_remaining_seconds"] == cached
