# Installing / upgrading to v0.10.12

v0.10.12 is the maintenance release that came out of the deep code review.
The headline fix: the Windows job-object cleanup that was supposed to kill
the whole process tree even when the Studio crashed never actually engaged -
the struct was too small, so creation failed silently and every managed run
fell back to `taskkill`. That is fixed and verified with tests. The release
also stops the chat from echoing your prompt back as "AI output", makes
chat validation clean up its process tree, and makes warmup validation
resume-aware.

## Fresh install (from source)

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python.exe -m godot_coder.doctor
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m godot_coder.studio
```

## Upgrading an existing install (from v0.10.11)

Copy the changed files from the release into your install, then restart
the Studio:

```
CHANGELOG.md                        # v0.10.12 entry
docs/CHANGELOG_v0.10.12.md          # new patch notes
docs/INSTALL_v0.10.12.md            # this file
src/godot_coder/process_control.py  # job-object struct + rights + fallback
src/godot_coder/ui/services.py      # managed validation + prompt strip
src/godot_coder/ui/server.py        # dead TimeoutExpired clause removed
src/godot_coder/config.py           # resume-aware warmup validation
src/godot_coder/train.py            # CPU peek of resume step
src/godot_coder/data.py             # explicit memmap close
src/godot_coder/ui/static/app.js    # empty-completion hint
src/godot_coder/ui/static/sw.js     # cache bump
src/godot_coder/__init__.py         # version marker
pyproject.toml                      # version (optional)
tests/test_validation_watchdog.py   # job-object struct/rights tests
tests/test_ui_services.py           # new: managed validation + prompt strip
tests/test_professional_core.py     # resume-aware warmup test
```

Or, on a release distribution, the packaged upgrade script
(`APPLY_*.ps1`) handles the file list for you.

## After upgrading

- `python -m pytest -q` should report **206 passed**.
- Restart the Studio so the server picks up the changed modules.
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.12-1`). If anything still looks stale, hard-refresh with
  Ctrl+Shift+R.
- No data re-processing is needed - these are runtime/process fixes, not
  dataset changes. The next time a job runs, managed process cleanup
  actually works, and hung Godot checks no longer leave orphans.
- If you have an old `RECOVER_STUCK_VALIDATION.bat` lying around from the
  orphan-workaround days, it is now redundant - the chat validator cleans
  up its own tree.
