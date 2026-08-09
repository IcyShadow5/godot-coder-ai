# Installing / upgrading to v0.10.11

v0.10.11 fixes a quiet data-integrity gap: chat samples saved from the
Studio (`data/raw/user_lessons`) were staged into the corpus but never
actually checked by Godot - a broken sample could slip into the training
data. They are now resolved against the project root and get the same
per-file parse as everything else. The release also splits the Studio UI
into modules (no behavior change) and adds the first honest results
section to the README.

## Fresh install (from source)

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python.exe -m godot_coder.doctor
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m godot_coder.studio
```

## Upgrading an existing install (from v0.10.10)

Copy the changed files from the release into your install, then restart
the Studio:

```
CHANGELOG.md                        # v0.10.11 entry
README.md                           # recent changes + first results
docs/CHANGELOG_v0.10.11.md          # new patch notes
docs/INSTALL_v0.10.11.md            # this file
src/godot_coder/corpus.py           # user-lessons validation fix
tests/test_project_aware_corpus_validation.py  # 2 regression tests
tests/test_corpus.py                # user-lessons ingestion test
src/godot_coder/ui/static/app.js    # slimmer after the module split
src/godot_coder/ui/static/api.js    # new: shared UI helpers
src/godot_coder/ui/static/remote.js # new: remote-tab section
src/godot_coder/ui/static/index.html     # loads the new modules
src/godot_coder/ui/static/sw.js     # SHELL list + cache bump
tests/test_ui_remote_assets.py      # checks all four UI modules
src/godot_coder/__init__.py         # version marker
pyproject.toml                      # version (optional)
```

Or, on a release distribution, the packaged upgrade script
(`APPLY_*.ps1`) handles the file list for you.

## After upgrading

- `python -m pytest -q` should report **198 passed**.
- Restart the Studio so the server picks up the new corpus.py.
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.11-1`). If the chat buttons still look stale, hard-refresh with
  Ctrl+Shift+R.
- The corpus itself does not need a rebuild for the fix to matter - the
  change is in how validation resolves files, so the next `validate`
  pass already uses it. Chat samples you save from now on are checked
  for real.
