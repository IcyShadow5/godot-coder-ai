# Changelog

Patch notes, so I still know later what I was thinking.

## v0.10.8 (2026-08-09)

AMD GPUs and a clean exit.

### Added

- **AMD ROCm (HIP) support.** `rocm_available()` detects a HIP build via
  `torch.version.hip` (ROCm builds expose AMD GPUs under the *cuda* device
  namespace, so `auto` already lands on them - this adds detection, an
  explicit `device: rocm` request that maps onto `cuda`, and honest labels).
  The Studio system panel reports **ROCm ready** with the AMD GPU name and a
  ROCm/HIP build row; `doctor` reports `pytorch_hip_build`; the README and
  CONFIG_REFERENCE document the new device value.

### Fixed

- **prepare_data hung after finishing.** `main()` ended with
  `print(json.dumps(manifest, indent=2))` and the manifest - with per-document
  metadata for every file - is ~10MB. When stdout is a pipe nobody drains
  (background launch), that write blocks forever on the full pipe buffer: all
  shards and `manifest.json` land on disk, then the process sits frozen at
  zero CPU instead of exiting. It now prints a one-line summary (tokens per
  split, fingerprint) and exits cleanly. The full manifest stays on disk in
  `manifest.json`.

### Changed

- Five new tests: four for ROCm device selection
  (`tests/test_runtime_device.py`) and one regression that runs the
  prepare_data CLI with piped stdout and asserts a prompt, small-summary exit
  (`tests/test_data.py`). 188 -> 193 tests.

### Version

- `__init__.py`, `pyproject.toml` and the service worker cache bumped to
  `0.10.8`.
