# Installing / upgrading to v0.10.18

v0.10.18 is a small review-backlog release: the system panel now shows
whether `torch.compile` is usable at a glance, preflight no longer crashes
on a fresh project without a tokenized stream (it answered 500 instead of
a blocker), and 24 new tests close the gaps in the chat, config, preflight
and jobs endpoints.

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
docs/CHANGELOG_v0.10.18.md
docs/INSTALL_v0.10.18.md
CHANGELOG.md
README.md
mkdocs.yml                       # nav -> v0.10.18 docs
pyproject.toml                   # version bump
src/godot_coder/__init__.py      # version marker
src/godot_coder/ui/static/sw.js  # cache bump
src/godot_coder/ui/services.py   # compile_available in system_status
src/godot_coder/ui/static/app.js # system panel torch.compile card
src/godot_coder/corpus_audit.py  # preflight None-crash fix
tests/test_ui_server_stream.py   # +5 streaming/overview tests
tests/test_profile_probe_compile.py  # +3 compile-fallback tests
tests/test_ui_route_gaps.py      # +16 endpoint tests
```

## After upgrading

- `python -m pytest -q` should report **369 passed**.
- Restart the Studio so the server picks up the changed modules.
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.18-1`). If anything still looks stale, hard-refresh with
  Ctrl+Shift+R.
- The system tab now shows a `torch.compile` card - "available · triton
  x.y.z" when the compile extra is installed, "not installed" otherwise.
- No data re-processing is needed. Existing datasets and checkpoints stay
  valid.
