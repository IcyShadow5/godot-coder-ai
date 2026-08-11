# Changelog

## v0.10.7 (2026-08-08)

Cross-platform release. The CI matrix on Ubuntu + Windows + macOS caught bugs
that only show up off my machine, so this version is about the suite running
everywhere - not just on my Windows box.

### Fixed

- **Windows job-object tests tested the wrong code.** Four tests patched
  `os.name = "nt"` but called the *originally imported* `_windows_*`
  functions, so the patch silently did nothing. On Linux the real code raised
  inside its own `except Exception: pass`, the asserted cleanup never ran, and
  - because `os.name` was still `"nt"` while pytest formatted the failure -
  pytest itself crashed building a `WindowsPath`. The tests now fake kernel32
  with plain functions (which accept `.argtypes`/`.restype`), work on every
  platform, and `monkeypatch.setattr(..., raising=False)` makes the
  Windows-only `ctypes.WinDLL` patch apply (and tear down) on Linux/macOS too.
- **macOS reported dead processes as alive.** `process_is_alive` only knew
  how to spot zombies via `/proc/PID/stat`, which macOS and BSD don't have.
  There, `os.kill(pid, 0)` returns success for a zombie, so the termination
  wait-loop spun to its deadline and validation crash-recovery falsely
  reported failure. The function now reads `ps -p PID -o stat=` when `/proc`
  is absent and treats state `Z` (zombie) / `X` (dead) as not alive.
- **macOS truncated command lines.** `ps -o command=` caps the width at
  132 chars by default, cutting off exactly the tail (`godot --path ...`)
  the recovery check needs. Now uses `-ww` and the canonical `-o args=`.
- **CI actions deprecated.** `actions/checkout@v4` and `actions/setup-python@v5`
  were forced onto Node 24; bumped both to v7, silencing the warning (same
  change Dependabot proposed).

### Changed

- **CI matrix:** the full suite now runs on `ubuntu-latest`, `windows-latest`
  and `macos-latest` (CPU torch, `fail-fast: false`). The matrix is what found
  these bugs - without it, macOS would have shipped broken.
- **Apple Silicon (MPS) support:** device auto-selection is now `cuda` -> `mps`
  -> `cpu`; an explicit `mps` request raises cleanly when unavailable. The
  Studio system panel reports "MPS ready" + unified memory instead of a
  misleading "CPU mode". AMP stays CUDA-only, so MPS runs fp32 safely.
- **`process_command_line`** falls back to `ps` on macOS/BSD (no `/proc`).
- Four new device tests (`tests/test_runtime_device.py`) + one process test
  (`tests/test_validation_watchdog.py`). 183 -> 188 tests.

### Version

- `__init__.py`, `pyproject.toml` and the service worker cache bumped to
  `0.10.7`.
