# Changelog

## v0.10.15 (2026-08-10)

The second batch from the external review. This one was almost entirely
robustness: six findings, each with a real consequence, each fixed with a
regression test that fails on the old code.

### Fixed

- **Fast Import Mode leaked its temp working copy.** `_validate_project`
  returned early on `GODOT_CODER_SKIP_PROJECT_IMPORT=1` before the cleanup
  ran, so every fast import left a full project copy in the temp folder.
  The cleanup now runs on both paths. The new regression test surfaced
  144 accumulated leftovers on my machine - cleaned up.
- **The GitHub archive path had no zip-bomb guard.** `_safe_extract_github_archive`
  only checked path traversal; the size/entry/ratio limits lived in
  `local_sources.py`. The preflight moved to `corpus.py` (the lower layer),
  both importers share it, and the GitHub extractor now runs it before
  anything touches the disk. Two new tests.
- **Instruction data only ever used the first function per file.** The
  `if added: break` killed the outer function loop, silently dropping
  most of the material in multi-function files. `max_tasks_per_file` now
  really means per file. Regression test.
- **The audit checkpoint was written but never read.** A crash mid-audit
  restarted at record 1 and re-ran every Godot validation. `audit_corpus`
  now loads the checkpoint, verifies a manifest fingerprint, rebuilds the
  dedup/leakage state deterministically and resumes where it stopped.
- **Checkpoints loaded with `weights_only=False` (CWE-502).** The only
  blocker was the numpy RNG key inside the payload. New checkpoints store
  it as a plain list of ints and load fully safe; legacy checkpoints load
  through a scoped numpy-only allowlist. The unsafe fallback is gone and
  the real v06_balanced checkpoints still load.
- **Progress events could be suppressed by index 0.** 22 guards used
  `if progress and project_index:`; a falsy 0 would silently kill every
  event for that project. Now explicit `is not None` checks. Regression
  test drives `project_index=0` directly.

### Housekeeping

- Unused-import cleanup: `studio.py` imports `find_project_root` directly
  from `.project`, `ui/paths.py` dropped its re-export, and the golden-tasks
  import test is now a real signature check.
- `.gitignore` covers the runtime noise (processed data, runs, checkpoints,
  logs).

263 tests pass.
