# Changelog

Patch notes, so I still know later what I was thinking.

## v0.10.4 (2026-08-08)

### Fixed

- **Hard syntax errors could still slip through as warnings.** Found both
  leaks while auditing the previous validation run. The hard-error list only
  knew `expected ...` phrasing, so unambiguous errors like `Invalid statement.`
  were kept as context warnings and ended up in the dataset. The marker list
  now also covers `invalid use of`, `invalid assignment`,
  `expected identifier`, `expected variable name`, `constant expected` and
  `invalid declaration`.
- **A benign context error could demote a real parse error.** Hard vs.
  context was decided on the whole +/- 3 line block around the error - a
  missing resource or RID leak printed next to a real parse error turned it
  into a warning. Only the error line itself decides now; the block is only
  used for the message text and the path attribution.
- **The generated checker failed on strict projects.** gdUnit4 promotes
  GDScript warnings to errors, and the checker's untyped loop variable
  (`for path_value in paths:`) did not compile there - every file of such a
  project stayed unverified as a context warning. The checker now carries an
  `@warning_ignore_start(...)` region and explicit types
  (`Array[String]`, `for path_value: String in paths:`); verified against
  gdUnit4 with Godot 4.7.
- **Stale German catalog titles.** Manifest records and source manifests that
  still carried `Offizielle Godot-Demoprojekte` are refreshed from the
  English registry during validation. Local user sources are never touched.

### Changed

- Validator cache bumped to v3 (`godot_project_validation_v3.json`): the
  next `validate` re-checks every project with the corrected classifier
  instead of replaying the old lenient decisions.
- `index.html` now declares `lang="en"` (was `de`).

### Version

- `__init__.py`, `pyproject.toml` and the service worker cache bumped to
  `0.10.4`.
