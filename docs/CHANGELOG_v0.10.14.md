# Changelog

Patch notes, so I still know later what I was thinking.

## v0.10.14 (2026-08-09)

An external reviewer ran a full static + runtime pass over the release zip
and came back with a short list. Most of it was hygiene, but two findings
were genuinely worth fixing: the dataset swap in `prepare_dataset` could
destroy a working dataset if the process died mid-replace, and the
`/proc/<pid>/stat` parser misread process names that contain spaces. Both
are fixed now, with regression tests.

### Fixed

- **`prepare_dataset` swap is now crash-safe.** It used to delete the old
  shards first and then move the new ones in - a crash in between left a
  half-deleted dataset that no longer matched its manifest. It now stages
  the complete new dataset (shards, aliases and manifest) and swaps it in
  with a backup + rollback, mirroring `corpus._replace_directory`. A
  leftover `.previous` directory from a crashed run is restored before the
  next build.
- **`/proc/<pid>/stat` parsing handled spaces in process names wrong.** The
  stat format is `pid (comm) state ppid ...` and `comm` may contain spaces,
  which shifted every field after it. A process named e.g. `my proc` was
  silently dropped from the descendant tree (so it survived a timeout
  kill), and the zombie check never fired. Both parsers now cut `comm` out
  between the parens before splitting.
- **Flaky test fixed.** `test_managed_process_timeout_terminates_descendant_tree`
  patched `process_is_alive` on the module but then called the name it had
  bound at import time, so the patch never took effect and the assertion
  raced real OS timing. Now patched and asserted through the module
  reference. Runs green 6/6 fresh.

### Cleanup

- **Unused imports removed** (10 spots): `Iterable` in local_sources,
  `json` in prepare_data, `math` in profile_probe, `signal` in jobs, a
  local `ctypes` in process_control, a lazy `torch` in server.token_stream,
  unused names in ui/paths and a few test files. One flagged import
  (`run_benchmark` in test_golden_tasks) is intentionally kept - the import
  is the test.
- **Semicolon chains split** in train.py (36 statement chains, one suite
  line left alone). Functionally identical, but debuggers and diffs are
  much happier.
- **Line endings normalized to LF** across 20 files that were still CRLF.
  The repo always declared LF via `.gitattributes`; the blobs now match it.
- **`mask_secrets` covers more ground** - GitHub tokens (`ghp_...`), JWTs
  and `user:password@` connection strings are redacted too.
- **STUDIO.md version header** was stale (v0.10.12).

### Tests

- 254 pass (3 new): the two `prepare_dataset` crash-safety tests and the
  secret-masking coverage test.

### Version

- `__init__.py`, `pyproject.toml`, the service worker cache and STUDIO.md
  bumped to `0.10.14`.
