# Installing / upgrading to v0.10.9

v0.10.9 is a docs-only release: a voice pass over the documentation
(translation artifacts removed, stale version stamps bumped). No code changed,
so an upgrade is optional - take it to keep docs and version markers current.

## Fresh install (from source)

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python.exe -m godot_coder.doctor
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m godot_coder.studio
```

## Upgrading an existing install (from v0.10.8)

Copy the changed files from the release into your install, then restart the
Studio:

```
CHANGELOG.md                        # v0.10.9 entry
README.md                           # recent changes + doc links
STUDIO.md                           # version stamp + wording
ARCHITECTURE.md                     # version stamp
ROADMAP.md                          # stable scope stamp
docs/CHANGELOG_v0.10.9.md           # new patch notes
docs/INSTALL_v0.10.9.md             # this file
docs/INSTRUCTION_ROADMAP_v0.7.md    # wording
docs/PROGRESS_EVENT_SCHEMA_v1.md    # wording
src/godot_coder/__init__.py         # version marker
pyproject.toml                      # version (optional)
src/godot_coder/ui/static/sw.js     # service-worker cache bump
```

Or, on a release distribution, the packaged upgrade script (`APPLY_*.ps1`)
handles the file list for you.

## After upgrading

- `python -m pytest -q` should still report **193 passed** (no code touched).
- No other behavior changed - this release only refreshes docs and version
  markers.
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.9-1`).
