# Changelog

## v0.10.19 (2026-08-10)

Small release, mostly polish from the last review round: the system panel's
`torch.compile` card now cross-checks the autotuner's probe instead of only
doing a static import check, the three coverage gaps the reviewer flagged
are closed, and two tiny hygiene fixes landed.

### New

- **System panel prefers the autotune probe.** `/api/overview` now
  reads `reports/hardware/autotune_latest.json` and treats a fresh probe
  verdict as the ground truth in both directions: when the probe proved
  `compile` works, the panel says available even if the static Triton
  import check currently fails; when it proved a kernel-build failure,
  the panel says "not available · <reason>" instead of trusting that
  Triton merely imports. The static check only decides when no (fresh)
  report exists. The verdict is honored for 30 days - install the
  `[compile]` extra after a failed run and the panel flips back once the
  report is stale.

### Fixed

- **Unused `monkeypatch` params removed** in the two streaming-chat tests.
- **`except Exception` narrowed to `except ImportError`** in the compile
  availability probe (missing extra and broken DLL both surface as
  `ImportError`).

### Tests

- **12 new tests (381 total).** `test_ui_route_gaps.py` adds the last
  three missing endpoint cases: `PUT /api/config/raw` with an invalid
  `TrainConfig` (400), `PUT /api/corpus/sources` with valid and invalid
  payloads (license / id / url rules), and `POST /api/jobs/stop` with and
  without a running job. `test_ui_services.py` pins the new cross-check:
  no report -> static wins, probe `False` overrides static `True`, stale
  probe falls back, malformed report never crashes.

### Chores

- Version bumped to 0.10.19, service worker cache bumped to `v0.10.19-1`,
  docs updated (`docs/CHANGELOG_v0.10.19.md`, `docs/INSTALL_v0.10.19.md`,
  mkdocs nav).

381 tests.
