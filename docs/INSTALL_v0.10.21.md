# Installing / upgrading to v0.10.21

v0.10.21 is a test-coverage round that surfaced one real bug: `GET
/api/corpus/scale-plan` had a broken relative import since the router
refactor (v0.10.16) and always answered 500. That is fixed and verified
live. The suite grew by 83 tests (465 total) to cover the training/corpus
router handlers, `doctor.py` and `profile_probe.py`.

## Fresh install (from source)

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
# Optional: torch.compile speedup (probe + trainer pick it up automatically):
.venv\Scripts\pip install -e ".[compile]"
.venv\Scripts\python.exe -m godot_coder.doctor
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m godot_coder.studio
```

## Upgrading an existing install

Replace these files from the release zip:

```
docs/CHANGELOG_v0.10.21.md
docs/INSTALL_v0.10.21.md
CHANGELOG.md
README.md
mkdocs.yml                          # nav -> v0.10.21 docs
pyproject.toml                      # version bump
src/godot_coder/__init__.py         # version marker
src/godot_coder/ui/static/sw.js     # cache bump
src/godot_coder/ui/routers/corpus.py  # scale-plan import fix
tests/test_ui_training_router.py    # new: training router handler tests
tests/test_ui_corpus_router.py      # new: corpus router tests
tests/test_doctor.py                # extended: CUDA paths + main()
tests/test_profile_probe.py         # new: probe pipeline tests
```

## After upgrading

- `python -m pytest -q` should report **465 passed**.
- Restart the Studio so the server picks up the changed modules.
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.21-1`). If anything still looks stale, hard-refresh with
  Ctrl+Shift+R.
- The corpus tab's scale-plan card now loads again (it 500'd since the
  v0.10.16 router refactor).
- No data re-processing is needed. Existing datasets and checkpoints stay
  valid.
