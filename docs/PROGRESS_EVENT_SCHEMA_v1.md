# Godot Coder AI Progress Event Schema v1

The schema transports observable work events between the CLI pipeline,
job management and the Studio. It is explicitly not a thought protocol: it only
contains technical states, counters, paths, results and time measurements that
arise during real execution. The Studio UI draws its progress and
remaining-time displays from it; the job management stores the events for later retrieval.

## Transport

CLI processes write one event per line in addition to normal text logs:

```text
GCAI_EVENT {"schema":"godot-coder-progress-event", ...}
```

The part after `GCAI_EVENT ` is a JSON object. The job management stores
events together with normal logs as JSONL under:

```text
reports/studio_jobs/<job_id>.log.jsonl
```

A recoverable snapshot is at:

```text
reports/studio_jobs/<job_id>.snapshot.json
```

The complete human-readable text log is at:

```text
reports/studio_jobs/<job_id>.log.txt
```

## Required fields after normalization

| Field | Type | Meaning |
|---|---|---|
| `schema` | String | Currently `godot-coder-progress-event`. |
| `schema_version` | Integer | Currently `1`. |
| `event` | String | Event name, e.g. `local_project_progress`. |
| `timestamp` | ISO-8601 string | UTC timestamp, always with `Z`. |
| `job_id` | String or `null` | Studio job ID, when available. |
| `level` | String | `info`, `warning` or `error`. |

## Common optional fields

| Field | Type | Meaning |
|---|---|---|
| `project_index`, `project_total` | Integer | Current project and total count. |
| `project_name` | String | Display name of the project. |
| `project_status` | String | Project state (see states). |
| `phase`, `phase_label`, `phase_status` | String | Machine name, readable name and state of the phase. |
| `file_index`, `file_total`, `remaining_files` | Integer | File progress. |
| `current_file` | String | Current relative file or current work step. |
| `scripts_found`, `trainable_scripts` | Integer | Detected and trainable GDScript files. |
| `passed`, `warnings`, `failed`, `quarantined` | Integer | Result counters. |
| `addon_files`, `generated_files` | Integer | Excluded add-on and cache/import files. |
| `accepted` | Integer | Accepted entries, e.g. in the corpus check. |
| `bytes_received`, `bytes_total` | Integer | Download progress in bytes (remote sources). |
| `next_project`, `next_project_scripts`, `next_phase` | mixed | Preview of the next work step. |
| `job_status` | String | Job end state, e.g. `completed` (remote sources). |
| `source_url`, `source_name` | String | Shortened URL or file name of the remote source (always masked). |
| `elapsed_seconds` | Number | Time elapsed since start. |
| `estimated_remaining_seconds` | Number or `null` | Mean remaining-time estimate. |
| `estimated_remaining_min_seconds`, `estimated_remaining_max_seconds` | Number or `null` | Optional estimate span. |
| `overall_progress`, `project_progress` | Number 0-1 | Overall and project progress. |
| `message` | String | Human-readable status message. |
| `return_code` | Integer or `null` | Exit code of a technical subprocess. |
| `command`, `parser_output`, `failure_reason` | mixed | Technical view; masked before storing. |

Unknown fields are preserved so new producers can deliver additional
information without breaking old consumers. Missing
optional fields are not assumed. Number fields are normalized
(negative or non-numeric -> `null`), `overall_progress`/`project_progress`
are clamped to the range 0-1.

## States

Supported project/phase states:

```text
waiting
running
passed
passed_with_warnings
failed
quarantined
disabled
skipped
completed
stopped
```

## Phases

### Private import (local_sources.py)

| Machine name | Display |
|---|---|
| `input_detection` | Detect ZIP or folder |
| `secure_extract` | Extract safely |
| `project_detection` | Detect `project.godot` |
| `inventory` | Inventory files |
| `cache_exclusion` | Exclude cache and import files |
| `addon_classification` | Classify add-ons |
| `secret_scan` | Secret scan |
| `file_size_check` | File size check |
| `static_analysis` | Static GDScript check |
| `deduplication` | Deduplicate source |
| `corpus_admission` | Adopt cleaned working copy |
| `godot_validation` | Godot project import and parser check |
| `quarantine_decision` | Quarantine decision |
| `registry_update` | Update corpus registry |
| `report_writing` | Write final report |

### Corpus check (corpus.py)

| Machine name | Display |
|---|---|
| `corpus_validation` | Project-based corpus validation |

### Remote sources (remote_sources.py)

| Machine name | Display |
|---|---|
| `remote_link_validation` | Check remote link safely |
| `remote_download` | Download source to this PC |

## Event names

| Producer | Events |
|---|---|
| `local_sources.py` | `local_import_started`, `local_import_plan`, `local_import_item`, `local_import_completed`, `local_import_failed`, `local_import_registry`, `local_import_report`, `local_project_started`, `local_project_phase`, `local_project_progress`, `local_project_completed`, `local_project_failed`, `local_validation_cleanup`, `local_validation_recovery` |
| `corpus.py` | `corpus_validation_progress`, `corpus_validation_completed` |
| `remote_sources.py` | `remote_source_started`, `remote_source_progress`, `remote_source_completed` |

Consumers may only rely on `event` + `phase` + `phase_status` + counters;
the exact field combination per event may grow.

## Example

```json
{
  "schema": "godot-coder-progress-event",
  "schema_version": 1,
  "event": "local_project_progress",
  "timestamp": "2026-08-05T14:42:00Z",
  "job_id": "local-source-import-abc123",
  "level": "info",
  "project_index": 2,
  "project_total": 5,
  "project_name": "Embercore",
  "project_status": "running",
  "phase": "static_analysis",
  "phase_status": "running",
  "file_index": 14,
  "file_total": 18,
  "current_file": "scripts/player.gd",
  "passed": 13,
  "warnings": 1,
  "failed": 0,
  "quarantined": 0,
  "elapsed_seconds": 102.0,
  "estimated_remaining_seconds": 145.0,
  "estimated_remaining_min_seconds": 116.0,
  "estimated_remaining_max_seconds": 174.0
}
```

## Remaining-time estimate

A remaining time is only emitted once enough measurements are available
(at least three file durations or one completed project duration). Primarily
observed file durations are used; completed project durations supplement
the estimate for remaining projects. With variable runtimes
minimum and maximum are delivered as a span. Until then the UI shows
`Calculating remaining time...`.

Important: If no remaining time is left between two projects (`remaining_files`
drops to 0), the last known estimate is kept instead of falling back to
"calculating". Otherwise the display flickers between two states at every
project change.

## Security

Before output, persistence and export, typical API keys, AWS keys,
bearer tokens, password/token assignments and PEM private keys are replaced
with `[REDACTED]`. Detection results must not contain the found secret values
themselves. This also applies to remote sources: URLs are shortened to
host + path before logging (`safe_url_for_log`), query parameters and fragments
are not output.

## Backward compatibility

- Normal text logs continue unchanged.
- Old jobs without events are still displayed.
- For known old lines like `local_import=2/5` and `local_project=...`
  a limited compatibility parser exists in the job management.
- Structured events are the primary progress source once
  they are present.
- Consumers must ignore unknown fields and tolerate missing
  optional fields.
