# Changelog

## v0.10.11 (2026-08-09)

Chat samples actually get verified now, and the Studio UI stopped being one
giant file.

### Fixed

- **Chat samples saved to `data/raw/user_lessons` were never checked by
  Godot.** The feedback loop wrote failed generations to
  `data/raw/user_lessons`, staging ingested them, but validation resolved
  every record against `downloads/<source_id>/<original_path>` - a path
  that does not exist for chat samples. They silently fell into the
  "source file missing" context-warning branch, so a broken sample would
  have flowed straight into the training data without ever being parsed.
  The new `_record_script_location()` helper resolves user-lessons
  records against the project root (where the raw chat file actually
  lives) and keeps the downloads-based resolution for registry sources.
  A hard syntax error in a chat sample is now a hard exclusion, exactly
  like everywhere else.
- **Two regression tests** for that path: a broken user-lessons sample is
  excluded (`failed` + `syntax_error`), and a valid one is kept. 198
  tests total.

### Changed

- **The Studio UI was split into modules.** `app.js` (1,961 lines) is now
  `api.js` (shared helpers: `api`, `toast`, `escapeHtml`, formatting),
  `remote.js` (the whole remote-tab section) and a slimmer `app.js`.
  Load order is `progress.js -> api.js -> remote.js -> app.js`, wired in
  `index.html` and the service-worker `SHELL` list. No behavior changed -
  the tests that assert UI assets now check all four modules.
- **README got a "First Results (so far)" section** with the honest
  numbers from the first real training run (val loss 1.78, perplexity
  5.96, 6.25% benchmark parse rate) so progress is measurable instead of
  guessed.

### Version

- `__init__.py`, `pyproject.toml` and the service worker cache bumped to
  `0.10.11`.
