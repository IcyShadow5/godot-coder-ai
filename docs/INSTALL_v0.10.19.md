# Installing / upgrading to v0.10.19

v0.10.19 is a small polish release: the system panel's `torch.compile` card
now cross-checks the autotuner's probe result (so a proven kernel-build
failure shows up as "not available" with the reason), the last three UI
coverage gaps are closed, and two tiny hygiene fixes landed.

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
docs/CHANGELOG_v0.10.19.md
docs/INSTALL_v0.10.19.md
CHANGELOG.md
README.md
mkdocs.yml                       # nav -> v0.10.19 docs
pyproject.toml                   # version bump
src/godot_coder/__init__.py      # version marker
src/godot_coder/ui/static/sw.js  # cache bump
src/godot_coder/ui/services.py   # compile_available cross-check + reason
src/godot_coder/ui/static/app.js # system panel card shows the probe reason
tests/test_ui_route_gaps.py      # +6 endpoint tests (config/sources/jobs)
tests/test_ui_server_stream.py   # unused monkeypatch params removed
tests/test_ui_services.py        # +6 cross-check tests
```

## After upgrading

- `python -m pytest -q` should report **381 passed**.
- Restart the Studio so the server picks up the changed modules.
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.19-1`). If anything still looks stale, hard-refresh with
  Ctrl+Shift+R.
- The system tab's `torch.compile` card now reflects the last autotune
  probe: "available · triton x.y.z" when the probe succeeded, "not
  available · <reason>" when it proved a kernel-build failure.
- No data re-processing is needed. Existing datasets and checkpoints stay
  valid.
