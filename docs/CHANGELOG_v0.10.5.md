# Changelog

Patch notes, so I still know later what I was thinking.

## v0.10.5 (2026-08-08)

### New

- **No record is ever kept unverified anymore.** A `context_warning` means a
  script could not be positively checked inside its project (the resource did
  not load, no checker marker was produced, or the project import/checker
  itself failed or timed out). Every such record is now additionally parsed
  standalone with Godot `--check-only`:
  - a real syntax error still becomes a hard exclusion (`syntax_error`), and
  - a clean parse keeps the record as a verified context warning, with the
    per-file result appended to the warning text.
  Only a truly missing source file skips the parse - and that is recorded
  explicitly instead of being silent.

### Changed

- Validator cache bumped to v4 (`godot_project_validation_v4.json`). The v3
  decisions predate the per-file step, so the next `validate` re-checks every
  project with the complete pipeline.

### Version

- `__init__.py`, `pyproject.toml` and the service worker cache bumped to
  `0.10.5`.
- The v0.10.5 upgrade package is cumulative from v0.10.3: one apply brings
  every v0.10.4 and v0.10.5 change, one re-`validate` re-checks with v4.
