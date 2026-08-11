# Changelog

## v0.10.20 (2026-08-10)

Follow-up to v0.10.19: the panel's compile cross-check now *prefers* the
autotune probe verdict in both directions, preflight stops listing
irrelevant corpus stages on a fresh project, and the release pipeline is
now fully automated - pushing a `v*` tag builds the zip and publishes the
GitHub release on its own.

### New

- **Panel prefers the autotune probe verdict.** `/api/overview` now treats
  a fresh probe result in `reports/hardware/autotune_latest.json` as the
  ground truth in both directions: a probe-proven kernel-build failure
  shows "not available · <reason>" (as before), and a probe-proven success
  now wins even if the static Triton import check currently fails. The
  static check only decides when no (fresh) report exists; the 30-day
  staleness bound still applies, so installing the `[compile]` extra after
  a failed run flips the panel back once the report is stale.

- **Automatic release workflow.** `.github/workflows/release.yml` fires on
  `v*` tag pushes: it builds `godot-coder-ai-<tag>-full.zip` from the
  tracked files via `git archive` (same file set as the old manual
  script) and creates the GitHub release with `docs/CHANGELOG_<tag>.md`
  as notes. Tagging a release is now a one-liner.

### Fixed

- **Preflight listed irrelevant corpus stages on a fresh project.** With
  no tokenized stream yet, `_pipeline_freshness` still reported the five
  corpus pipeline artifacts in `stages` even though there was nothing to
  judge against. It now reports `stages: {}` for a missing manifest;
  synthetic and corpus streams keep their existing stage sets.

### Tests

- **382 total** (one new): `test_pipeline_freshness_stages_for_each_
  manifest_state` pins the three branches - empty stages for a fresh
  project, `raw_source` only for synthetic streams, and the full corpus
  stage set for corpus-derived streams.

### Chores

- Version bumped to 0.10.20, service worker cache bumped to `v0.10.20-1`,
  docs updated (`docs/CHANGELOG_v0.10.20.md`, `docs/INSTALL_v0.10.20.md`,
  mkdocs nav).

382 tests.
