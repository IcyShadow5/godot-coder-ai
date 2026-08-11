# Changelog

## v0.10.6 (2026-08-08)

### Fixed

- **Preflight accepted only the old validator revision.** The check pinned
  the validation report to `project-aware-v2`, but the pipeline has written
  `project-aware-v4` since v0.10.4. The moment a fresh v4 report landed, every
  preflight - smoke and full - was blocked. The check now accepts any
  `project-aware-v*` revision.
- **Freshness judged datasets by inputs they never had.** The staleness gate
  compared the token stream against corpus pipeline stages (validation, audit,
  tokenizer reports). Synthetic datasets such as the curriculum keep their own
  raw source and were flagged stale by corpus files they never depend on. The
  check is now dataset-aware: corpus-derived streams compare against the
  corpus stages; everything else compares against its own `data/raw/<name>`.
- **Token minimums gated synthetic runs.** Full-mode preflight demanded
  ≥500k tokens for the default profile - wrong for a deliberately small
  curriculum set (88k tokens). The gate now applies to corpus-derived profiles
  (starter/balanced/experimental); synthetic streams pass it.

The `_is_corpus_stream()` helper centralises the “is this corpus data?”
decision and is shared by both checks.

### Changed

- Five new preflight regression tests (`tests/test_professional_core.py`),
  covering the v4 validator, dataset-aware freshness, and the corpus-only
  token gate. 178 → 183 tests.
- Publish-ready housekeeping: pyproject metadata (`readme`, `license`,
  classifiers), a CI workflow (full mocked test suite on Ubuntu), community
  files (`CONTRIBUTING`, `CODE_OF_CONDUCT`, `SECURITY`), issue and PR
  templates, README badges, and a consolidated root `CHANGELOG.md`.

### Version

- `__init__.py`, `pyproject.toml` and the service worker cache bumped to
  `0.10.6`.
