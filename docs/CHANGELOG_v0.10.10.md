# Changelog

Patch notes, so I still know later what I was thinking.

## v0.10.10 (2026-08-09)

Training-start fix - the crash only the passes-driven configs hit.

### Fixed

- **Starting training with `max_steps: null` crashed the Studio with an
  HTTP 500.** Passes-driven profiles (`autotuned_night`,
  `corpus_balanced_90m`) do not set a step limit - the run is driven by
  `target_dataset_passes`. The train endpoint called `int(None)` on the
  raw value, which raised a `TypeError` that was not handled, so the
  request died with a generic error and the "Start training" click did
  nothing. `max_steps` is now read as `int(raw.get("max_steps") or 0)` -
  null/absent behaves like "unbounded, passes-driven" and an explicit
  step count still feeds the job progress bar.
- **Two regression tests** for the train endpoint: a config with
  `max_steps: null` starts (job created, `max_steps=None`) and an
  explicit `max_steps: 600` is forwarded to the job. 195 tests total.

### Version

- `__init__.py`, `pyproject.toml` and the service worker cache bumped to
  `0.10.10`.
