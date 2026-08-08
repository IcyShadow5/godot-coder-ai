# Changelog

Patch notes, so I still know later what I was thinking.

## v0.10.9 (2026-08-09)

Docs-only release - no code changes.

### Changed

- **Voice pass over the docs.** A review pass against the "does this read like
  I wrote it" bar found a handful of leftover translation artifacts and stale
  version stamps, all fixed:
  - `STUDIO.md` / `ARCHITECTURE.md` / `ROADMAP.md` - stale `v0.10.6` stamps
    bumped to `v0.10.9` (the current stable scope).
  - `docs/PROGRESS_EVENT_SCHEMA_v1.md` - "resp." (a German *beziehungsweise*
    that slipped in) -> "and"/"or"; "Comprehensible status message" ->
    "Human-readable status message".
  - `docs/INSTRUCTION_ROADMAP_v0.7.md` - "in principle" -> "essentially".
  - `STUDIO.md` - "afterwards ... follows" -> "then ... follows" in the
    Knowledge Building steps.
- README and CHANGELOG got the usual v0.10.9 entries.

### Version

- `__init__.py`, `pyproject.toml` and the service worker cache bumped to
  `0.10.9`.
