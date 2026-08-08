# Changelog

Patch notes, so I still know later what I was thinking.

## v0.10.3 (2026-08-08)

### Fixed

- **Corpus validation completely hung on large Mono projects.**
  This was the worst bug so far: When importing projects like Pixelorama
  (Godot Mono build), Godot starts a child process that inherits the stdout/stderr
  pipes. `corpus.py` used raw `subprocess.run(capture_output=...)` - on
  timeout only the direct child process is terminated, and `communicate()`
  then blocks forever because the pipe is held open by the orphaned grandchild.
  Result: 0% CPU, no children, no progress, a job that hung as "running"
  for hours. The solution already existed:
  `run_managed_process` in `process_control.py` terminates the complete
  process tree via Windows job objects. The corpus validation (project import
  and script checker) now runs through it - timeouts kill the tree, no orphans
  anymore, no deadlock.
- **`validate_dataset.py` had a broken `os.environ.copy()` line** because
  `import os` was missing. The error would have surfaced on the first run as a
  `NameError`; now the import is there.
- **A silent job hung in the Studio on "running" forever.** When a
  child process stops producing output (exactly the Mono case above), there was
  no mechanism to detect it. The JobManager now has a
  **stall watchdog**: If a job stays without output for longer than `GODOT_CODER_JOB_STALL_TIMEOUT_SECONDS`
  (default 20 minutes), the process tree is terminated and the job
  is marked as failed - with a reason in the UI. `0` disables the
  watchdog.

### Fixed (validation policy, retroactively)

- **Scripts from add-on repos without `project.godot` were wrongly hard-excluded.**
  Pure add-on repositories (dialogic, phantom-camera,
  godot-firebase and others) do not ship their own `project.godot` - they are
  plugged into a host project. Still, all their scripts were discarded as
  `missing_project` (351 records in the current corpus). That contradicted
  the own policy ("only unambiguous syntax errors and incompatible Godot-3 files
  are hard-excluded"). Now the corpus validation checks such files
  individually with Godot's `--check-only` parser (`--path` = source root) and keeps them
  as a context warning - only real syntax errors are still excluded.
- **`GODOT_CODER_VALIDATION_TIMEOUT_SECONDS` / `_IDLE_TIMEOUT_SECONDS` did not
  apply to the corpus check.** The README documents both variables
  (default 120s / 30s), but only the local import (`local_sources.py`) read them;
  `corpus.py` had 60/120/30 hardcoded. On large projects a
  legitimate import could be aborted after 60s. `corpus.py` reads the variables
  now itself (import 120s, checker >= 120s, idle 30s; adjustable per env variable).
- **Scripts outside the resolved project path** were silently passed as
  "passed". Now: context warning instead of silent pass.
- **`_godot_version`** still ran via raw `subprocess.run` (same
  orphan-risk class as the main hang) - now via the managed-process runner.
- Dead code removed (unused `_first_error` in `corpus.py`).

### Tests

- Regression tests for the hang: Validation now provably runs via the
  managed-process runner with the correct timeouts (import 120s, checker >= 120s, 30s idle), and a timeout does not discard records but
  evaluates them with a context warning.
- Test for the stall watchdog: A job that produces no output is terminated
  after the window expires and marked as `failed`.
- Existing corpus tests switched to the managed runner (now mocks
  `run_managed_process` instead of `_run`).

### Version

- `__init__.py`, `pyproject.toml` and the service worker cache bumped to `0.10.3`.
- `GODOT_CODER_JOB_STALL_TIMEOUT_SECONDS` documented in the README.

## v0.10.2 (2026-08-08)

### Fixed

- **FAST_STATIC skipped the secret scan.** That was the most dangerous
  point: `GODOT_CODER_FAST_STATIC=1` should only save the slow AST warning analysis
  but also completely skipped the secret scan. A single
  `.env` file with an API key would have wandered straight into the training corpus. Now
  the secret scan and the file size check run **always**; only the
  static analysis (`_static_warnings`) is fast.
- **`GODOT_CODER_SKIP_PROJECT_IMPORT` was ignored when `FAST_STATIC=1`
  was set.** The skip decision depended on `skip_import and not fast_static` -
  both set meant: still the full `--import`. Now the skip is
  solely decisive; `FAST_STATIC` has nothing to do with it.
- **`validate_dataset.py` had a hardcoded 30s timeout** and used raw
  `subprocess.run`. Timeouts could leave orphaned Godot processes behind on Windows.
  It now reads `GODOT_CODER_PARSER_FILE_TIMEOUT_SECONDS`
  (default 10s, as in the import pipeline) and runs via the
  managed-process runner with job-object cleanup.
- **Duplicate `encoding_damage` warning** in normal imports (once in the
  secret scan, once in the file loop) - now only once.
- **Test name in the README did not match**: `test_eta_preserves_last_estimate_on_zero_remaining`
  had a different name in the code. Renamed so README and tests match again.
- **`.gitignore` ignored `*.bat`/`*.ps1`**, although the start/upgrade scripts
  are tracked. Removed - scripts are source code, not a build artifact.

### New

- **Studio toggles for the fast import** (Knowledge Building -> Own projects):
  skip project import, skip AST analysis, tighten error abort (500 -> 60 lines). They are passed to the import job via `extra_env` - no shell access needed.
- **`upgrade/` folder in the repo**: `upgrade/template/APPLY_TEMPLATE.ps1`,
  `upgrade/template/build_TEMPLATE_payload.ps1` and a `README`. The
  upgrade package is therefore no longer only external but reproducibly buildable from the repo. The v0.10.1 package
  had never brought documentation, version and `progress_events.py` to the live installation - that is now corrected.
- **`configs/autotuned_night.example.yaml`** - template for autotuner results.
  Real `configs/autotuned_*.yaml` stay local (every PC measures different timings)
  and are gitignored.
- **`docs/INSTALL_v0.10.2.md`** - current install/upgrade guide
  (replaces `docs/archive/INSTALL_v0.7.9.md`).
- **Historical documents refreshed**: `docs/PROGRESS_EVENT_SCHEMA_v1.md`
  now describes all 18 phases with event names and download fields
  (`bytes_received`/`bytes_total`, `accepted`) and the ETA cache.
  `docs/AUDIT_v0.6.md` and `docs/INSTRUCTION_ROADMAP_v0.7.md` are marked as
  historical records and converted to the new documentation language.

### Version

- `__init__.py` and `pyproject.toml` bumped to `0.10.2`.

## v0.10.1 (2026-08-07)

### New / Fixed

- **Fast Import Mode**: `GODOT_CODER_SKIP_PROJECT_IMPORT=1` (per-file parser instead of
  full `--import`, ~4s/project instead of 13-37s) and `GODOT_CODER_FAST_STATIC=1`
  (skip the AST walk).
- **Error-rate abort**: hanging `--import` runs with >500 consecutive
  error lines are aborted and switched to the safe per-file parser check
  (`GODOT_CODER_ERROR_ABORT_THRESHOLD`).
- **ETA cache fix**: The remaining-time estimate stays stable between projects,
  instead of falling back to "Calculating remaining time".
- **Windows job objects** in `process_control.py`: terminated Godot process trees
  (incl. Mono children) can no longer leave orphans behind.
- **Per-file loop double processing** fixed (files were counted/checked twice in fast mode).
- Encoding-resilient read/write paths, atomic staging, memmap cleanup,
  parser crash recovery, OOM emergency checkpoint, audit snapshots and various
  UI fixes (search results, filter, advanced toggle, service worker cache).

## v0.7.x and older

Historical notes are in `docs/archive/` (e.g. `INSTALL_v0.7.9.md`,
`PARSER_FALLBACK_v0.7.5.md`). Changes before v0.7.4 are no longer documented
individually.
