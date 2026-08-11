# Changelog

## v0.10.16 (2026-08-10)

This one is a maintainability release - no new behavior, but the code
that hurt to read is readable now, and the test suite grew by 42 tests
to cover the last modules that had none at all.

### Refactored

- **`create_app` split into route-group routers.** `ui/server.py` went
  from 864 lines to a 155-line factory. The 55 routes now live in
  `ui/routers/`, grouped by area - remote, corpus, training, chat and a
  system/data bucket - each as a real FastAPI `APIRouter`. The request
  models moved to `ui/schemas.py` so the routers and the app module
  never import each other. Behavior is identical: I diffed the route
  table (method + path) against the old server and it matches exactly,
  and the Studio serves the same OpenAPI after the split. One note:
  FastAPI 0.141's lazy `include_router` shows included routes as
  `_IncludedRouter` objects in `app.routes`, so any route introspection
  has to flatten `original_router.routes`.
- **`JobManager._run` split into named helpers.** The 85-line runner is
  now a short skeleton around `_spawn_process`, `_mark_running`,
  `_drain_output`, `_finalize` and `_fail`. Every state transition,
  lock, and the stall-watchdog race handling moved verbatim - the
  job-progress tests (real subprocesses) stayed green throughout.
- **Last semicolon chains split.** `train.py` had one real statement
  chain left; a tokenize scan of the whole `src/` then found six more
  in `data.py` (the `x = ...; y = ...` pairs in token-stream batching).
  All split, zero behavior change - the package no longer contains any
  statement semicolon.

### Tests

- **42 new tests (339 total).** The module-to-test map showed four
  files with no direct test at all: `metrics.py` (the typed event
  collector from the security work - untested until now), `project.py`,
  `prepare_data.py` and `train.py`. New suites cover the metrics
  collector end-to-end (JSONL persistence, summary totals), project
  root detection, the prepare CLI (both tokenizer create/load paths),
  and the train helpers - including `evaluate()` in all three modes
  (sample, fixed, sliding).
- The `safe_child` guard test now scans the router modules too, and the
  `_local_import_extra_env` test follows its helper to `ui/schemas.py`.

### Cleanup

- **Dead code removed** (each verified to have zero references in the
  repo and in git history): the unused `_FINAL_STATUSES` constant in
  `jobs.py`, the never-called `encode_files()` leftover in `data.py`,
  and an unused `pytest` import in the doctor tests. Left alone on
  purpose: `EXACT_PROMPTS` / `TRANSFER_PROMPTS` and
  `_validation_retry_timeout_seconds`, which carry explicit
  backwards-compatibility comments.

339 tests.
