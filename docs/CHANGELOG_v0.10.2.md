# Changelog

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
