# Installing / upgrading to v0.10.6

v0.10.6 fixes three preflight blockers found while running the first real
training runs: the validator version check, dataset-aware freshness, and the
token-minimum gate for synthetic datasets.

## Fresh install (from source)

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python.exe -m godot_coder.doctor
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m godot_coder.studio
```

## Upgrading an existing install (from v0.10.5)

Copy the changed files from the release into your install, then restart the
Studio:

```
src/godot_coder/corpus_audit.py     # preflight fixes
tests/test_professional_core.py     # regression tests (optional)
src/godot_coder/__init__.py         # version marker
pyproject.toml                      # version + metadata (optional)
src/godot_coder/ui/static/sw.js     # service-worker cache bump (optional)
```

Or, on a release distribution, the packaged upgrade script (`APPLY_*.ps1`)
does the copy and backs up every replaced file to
`.upgrade_backups/v0.10.6-<timestamp>` automatically.

After upgrading, run `python -m pytest -q` once to confirm the install is
healthy, then restart the Studio so the server picks up the new code.
