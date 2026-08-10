# Changelog

Patch notes, so I still know later what I was thinking.

## v0.10.21 (2026-08-11)

A test-coverage round that found one real bug: the router refactor from
v0.10.16 quietly broke `GET /api/corpus/scale-plan` - a lazy relative
import with the wrong depth (`..scale_plan` instead of `...scale_plan`)
made the endpoint 500 every time. The new corpus router tests caught it,
the one-line fix is in, and the endpoint is verified live in the Studio.
On top of that, 83 new tests close the last untested corners of the
training/corpus routers, `doctor.py` and `profile_probe.py`.

### New

- **Training router tests.** `tests/test_ui_training_router.py` (11 tests)
  pins the launch contract of the five job-starting handlers (hardware
  probe/autotune, train, benchmark, prepare) through a recording fake - no
  real subprocess ever starts.

- **Corpus router tests.** `tests/test_ui_corpus_router.py` (30 tests)
  covers corpus source create / replace-delete / validation through the
  real registry validation, all eight corpus job starters (incl. the
  local-source import toggles and the 409 busy cases), and the status
  readouts.

- **Doctor coverage.** `tests/test_doctor.py` grew to 17 tests: the CUDA
  paths (pass / non-finite attention / runtime exception / build-but-
  unavailable), the Godot probe failure semantics (a crashed probe is a
  hard failure, a non-zero version probe is not) and `main()` end-to-end
  with JSON output and exit codes.

- **Probe coverage.** `tests/test_profile_probe.py` (33 tests) covers the
  whole probe pipeline: a real tiny CPU training step, the OOM fallback,
  `torch.compile` enable and fallback paths, the worker subprocess
  contract, and the report/recommendation logic.

### Fixed

- **Scale-plan endpoint (broken since v0.10.16).** `GET
  /api/corpus/scale-plan` imported `..scale_plan` from inside
  `ui/routers/corpus.py`, which resolves to `godot_coder.ui.scale_plan` -
  a module that does not exist. The import is now `...scale_plan`, the
  endpoint returns 200 again, and the fix is live in the Studio.

### Tests

- **465 total** (+83): the training/corpus router handlers, doctor and
  profile probe were the last modules the suite did not touch at all.

### Chores

- Version bumped to 0.10.21, service worker cache bumped to `v0.10.21-1`,
  docs updated (`docs/CHANGELOG_v0.10.21.md`, `docs/INSTALL_v0.10.21.md`,
  mkdocs nav).

465 tests.
