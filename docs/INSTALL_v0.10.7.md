# Installing / upgrading to v0.10.7

v0.10.7 is the cross-platform release: the test suite now runs on Windows,
Linux and macOS in CI, Apple Silicon gets real MPS acceleration, and process
handling on macOS/BSD was hardened (zombie detection, untruncated command
lines). No new pipeline steps - mostly "it used to only really run on my
Windows machine, now it provably runs everywhere".

## Fresh install (from source)

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python.exe -m godot_coder.doctor
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m godot_coder.studio
```

## Upgrading an existing install (from v0.10.6)

Copy the changed files from the release into your install, then restart the
Studio:

```
src/godot_coder/process_control.py   # zombie detection + ps -o args= (macOS/BSD)
src/godot_coder/runtime.py           # MPS support in device auto-selection
src/godot_coder/ui/services.py       # system status reports MPS
src/godot_coder/ui/static/app.js     # Studio panel shows MPS ready
src/godot_coder/ui/static/sw.js      # service-worker cache bump
tests/test_validation_watchdog.py    # rewritten Windows fakes + zombie test
tests/test_runtime_device.py         # new device tests (optional)
src/godot_coder/__init__.py          # version marker
pyproject.toml                       # version + metadata (optional)
README.md / CHANGELOG.md / docs/     # docs (optional)
.github/workflows/ci.yml             # 3-OS matrix (optional, CI-only)
```

Or, on a release distribution, the packaged upgrade script (`APPLY_*.ps1`)
handles the file list for you.

## After upgrading

- `python -m pytest -q` should report **188 passed**.
- On Apple Silicon, the Studio system panel should say **MPS ready** instead
  of "CPU mode".
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.7-1`).
