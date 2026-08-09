# Installing / upgrading to v0.10.14

v0.10.14 is a robustness release from an external review. The dataset swap
in `prepare_dataset` could leave a half-deleted dataset if the process died
mid-replace - it now stages and swaps with backup + rollback. The
`/proc/<pid>/stat` parser misread process names containing spaces, which
could drop processes from the descendant tree on timeout. Both got
regression tests. The rest is cleanup: unused imports, semicolon chains in
train.py, LF line endings, and broader secret masking (GitHub tokens, JWTs,
connection strings).

## Fresh install (from source)

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python.exe -m godot_coder.doctor
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m godot_coder.studio
```

## Upgrading an existing install (from v0.10.13)

Copy the changed files from the release into your install, then restart
the Studio:

```
CHANGELOG.md                        # v0.10.14 entry
docs/CHANGELOG_v0.10.14.md          # new patch notes
docs/INSTALL_v0.10.14.md            # this file
STUDIO.md                           # version header
src/godot_coder/data.py             # crash-safe prepare_dataset swap
src/godot_coder/process_control.py  # /proc stat parsing + ctypes cleanup
src/godot_coder/progress_events.py  # broader secret masking
src/godot_coder/train.py            # semicolon chains split
src/godot_coder/local_sources.py    # unused import
src/godot_coder/prepare_data.py     # unused import
src/godot_coder/profile_probe.py    # unused import
src/godot_coder/ui/jobs.py          # unused import
src/godot_coder/ui/paths.py         # unused import
src/godot_coder/ui/server.py        # unused import
tests/test_data.py                  # +2 crash-safety regression tests
tests/test_progress_events.py       # +1 secret-masking test
tests/test_validation_watchdog.py   # flaky-test fix
tests/test_benchmark.py             # unused import
tests/test_ui_services.py           # unused import
tests/test_remote_sources.py        # unused import
src/godot_coder/ui/static/sw.js     # cache bump
src/godot_coder/__init__.py         # version marker
pyproject.toml                      # version (optional)
```

Or, on a release distribution, the packaged upgrade script
(`APPLY_*.ps1`) handles the file list for you.

## After upgrading

- `python -m pytest -q` should report **254 passed**.
- Restart the Studio so the server picks up the changed modules.
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.14-1`). If anything still looks stale, hard-refresh with
  Ctrl+Shift+R.
- No data re-processing is needed - the dataset swap fix only changes how
  future `prepare_data` runs replace old shards. Existing datasets and
  checkpoints stay valid.
