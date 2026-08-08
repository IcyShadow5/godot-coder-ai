# v0.10.1 - Fast Import & Error-Rate Abort (2026-08-07)

The release that made big imports practical. Detailed notes for the
surrounding versions live in the other `CHANGELOG_v0.10.x.md` files; this one
records where the fast-import flags came from.

## Fast Import Mode

- **`GODOT_CODER_SKIP_PROJECT_IMPORT=1`** - skips Godot `--import` entirely;
  validates each `.gd` file individually via Godot's per-file parser.
  **Massive speedup**: ~4s/project vs 13-37s/project. For 800+ projects that
  cuts import from 5-7 hours to under 1 hour.
- **`GODOT_CODER_FAST_STATIC=1`** - skips the redundant per-file AST walk and
  byte counting during import. Secret scan, file-size check and dedup still
  run (see the v0.10.2 fix).

## Error-Rate Abort

Godot's `--import` can get stuck on broken addon projects, producing
thousands of error lines without progress - and the old `idle_timeout` never
triggered because errors count as output. Now:

- Consecutive error lines are tracked via regex (`ERROR:`, `SCRIPT ERROR:`,
  `Parse Error:`, `failed to load`, `invalid UID`, ...)
- The counter resets on progress markers (`[ XX% ]`, `first_scan_filesystem`)
- Default threshold: **500 consecutive errors**
  (`GODOT_CODER_ERROR_ABORT_THRESHOLD`)
- On abort: the Godot process tree is killed and the safe per-file parser
  takes over

## Bug Fixes

- **Per-file loop double-execution** - `_static_warnings()` used to run even
  with `FAST_STATIC=1`, processing every file twice. Now properly guarded.
- **ETA cache flickering** - the estimator preserves its last estimate when
  `remaining_files` hits zero, so the UI stops flashing "Calculating
  remaining time".
