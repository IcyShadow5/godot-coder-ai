# Changelog

Patch notes, so I still know later what I was thinking. Detailed notes for
each version live in `docs/CHANGELOG_v0.10.x.md`; this file is the quick tour.

## v0.10.6 (2026-08-08)

Preflight correctness fixes, found while running the first real training runs:

- Preflight accepts the current project-aware validator revision (it was
  pinned to `project-aware-v2` while the pipeline writes `v4`).
- Freshness only gates corpus-derived streams; synthetic datasets compare
  against their own raw source.
- The token-minimum gate applies to corpus profiles only, so small synthetic
  runs pass full preflight.
- Five new preflight regression tests (183 total).
- Publish-ready housekeeping: pyproject metadata, CI workflow, community
  files (CONTRIBUTING, CODE_OF_CONDUCT, SECURITY), issue/PR templates,
  README badges.

## v0.10.5 (2026-08-08)

No record is ever kept unverified: context-warning scripts are additionally
parsed standalone per file with Godot `--check-only`. Validator cache bumped
to v4.

## v0.10.4 (2026-08-08)

Hard syntax errors can no longer slip through (marker list widened, line-only
classification), the per-file checker survives strict projects like gdUnit4,
and stale German catalog titles are healed.

## v0.10.3 (2026-08-08)

Corpus validation can no longer hang forever on large Mono projects (managed
process runner + Studio stall watchdog). `validate_dataset.py` import bug fixed.

## v0.10.2 (2026-08-08)

FAST_STATIC no longer skips the secret scan; Studio toggles for fast import;
`validate_dataset.py` honors the shared timeout; upgrade packages built from
templates in `upgrade/`.

## v0.10.1 (2026-08-07)

Fast import mode, error-rate abort for stuck Godot imports, ETA cache fix,
MIT license, initial upgrade tooling.
