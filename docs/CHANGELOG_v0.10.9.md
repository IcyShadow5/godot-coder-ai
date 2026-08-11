# Changelog

## v0.10.9 (2026-08-09)

Docs and upgrade tooling - no product code changed (no `.py`/`.js`/config
files), so the 193 tests stay green.

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

### Changed (upgrade tooling)

- `upgrade/template/APPLY_TEMPLATE.ps1` - the interactive "Type JA"
  confirmation prompt is gone. The apply runs non-interactively (backup ->
  copy -> doctor/pytest), matching how the v0.10.8/v0.10.9 packages were
  actually built - it also works from a scheduled task or a double-click
  `.bat`.
- `upgrade/template/APPLY_TEMPLATE.bat` (new) - double-click wrapper that asks
  for the project path. Packages v0.10.7/v0.10.8 shipped one; the repo
  template now does too.
- `upgrade/README.md` - documents the `.bat` wrapper.

### Version

- `__init__.py`, `pyproject.toml` and the service worker cache bumped to
  `0.10.9`.
