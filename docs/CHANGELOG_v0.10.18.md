# Changelog

## v0.10.18 (2026-08-10)

The review backlog, paid down. Two user-visible changes - the system panel
now shows compile availability, preflight no longer crashes on a fresh
project - and a big test-coverage push across the UI routes that the router
refactor reached but no test exercised directly.

### New

- **System panel shows `torch.compile` availability.** `/api/overview` now
  reports `compile_available` and the Triton version, and the Studio's
  system tab shows a "torch.compile: available · triton 3.7.1" card
  (or "not installed" when the compile extra is missing). It is the same
  signal the autotuner's compile probe proves for real - a quick static
  check (torch.compile exists + Triton importable) instead of a full probe
  run, so the panel updates instantly and never crashes the overview.

### Fixed

- **Preflight crashed on a fresh project.** With no tokenized stream and
  none of the default configs present, `build_preflight` passed `None` into
  the freshness check, which tried to read `data_manifest_path.parent` and
  died with an `AttributeError` - `/api/preflight` answered 500 instead of
  a blocker. It now answers properly ("Tokenized corpus training data
  missing"). Found by the new route tests.

### Tests

- **24 new tests (369 total).** `test_ui_server_stream.py` covers the
  streaming chat endpoint (token stream ending in `[DONE]`, 409 while a
  job runs, and the overview compile fields with and without Triton);
  `test_profile_probe_compile.py` pins the compile-fallback contract the
  autotuner relies on (a failing `torch.compile` reports
  `compile_enabled: false` + the reason instead of failing the probe);
  `test_ui_route_gaps.py` adds 16 endpoint tests for the non-streaming
  chat, the Godot validate endpoint, config read/write (including path
  escape and extension checks), preflight structure, jobs history/export
  and the checkpoints/configs lists.

### Chores

- Version bumped to 0.10.18, service worker cache bumped to
  `v0.10.18-1`, docs updated (`docs/CHANGELOG_v0.10.18.md`,
  `docs/INSTALL_v0.10.18.md`, mkdocs nav).

369 tests.
