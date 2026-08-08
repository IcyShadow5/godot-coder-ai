# Installing / upgrading to v0.10.10

v0.10.10 fixes a training-start crash: profiles driven by
`target_dataset_passes` (with `max_steps: null`) made the train endpoint
throw an HTTP 500, so the "Start training" click failed silently. That is
the one behavioral change - worth taking even if a run is already going.

## Fresh install (from source)

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python.exe -m godot_coder.doctor
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m godot_coder.studio
```

## Upgrading an existing install (from v0.10.9)

Copy the changed files from the release into your install, then restart
the Studio:

```
CHANGELOG.md                        # v0.10.10 entry
README.md                           # recent changes + doc links
docs/CHANGELOG_v0.10.10.md          # new patch notes
docs/INSTALL_v0.10.10.md            # this file
src/godot_coder/ui/server.py        # training-start fix (max_steps: null)
tests/test_ui_server.py             # 2 regression tests
src/godot_coder/__init__.py         # version marker
pyproject.toml                      # version (optional)
src/godot_coder/ui/static/sw.js     # service-worker cache bump
```

Or, on a release distribution, the packaged upgrade script
(`APPLY_*.ps1`) handles the file list for you.

## After upgrading

- `python -m pytest -q` should report **195 passed**.
- Restart the Studio so the server picks up the fix.
- A training run that is already going is not affected - the fix only
  matters when you press "Start training".
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.10-1`).
