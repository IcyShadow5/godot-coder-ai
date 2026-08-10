# Installing / upgrading to v0.10.20

v0.10.20 is a follow-up to v0.10.19: the system panel's `torch.compile`
card now fully prefers the autotune probe verdict (a proven success wins
even when the static Triton import check currently fails), preflight stops
listing irrelevant corpus stages on a fresh project, and the release
pipeline is automated - pushing a `v*` tag now builds the zip and
publishes the GitHub release by itself.

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
docs/CHANGELOG_v0.10.20.md
docs/INSTALL_v0.10.20.md
CHANGELOG.md
README.md
mkdocs.yml                       # nav -> v0.10.20 docs
pyproject.toml                   # version bump
src/godot_coder/__init__.py      # version marker
src/godot_coder/ui/static/sw.js  # cache bump
src/godot_coder/ui/services.py   # prefer-the-probe semantics
src/godot_coder/corpus_audit.py  # empty stages for fresh project
tests/test_professional_core.py  # +1 freshness branch test
.github/workflows/release.yml    # automatic release on tag pushes (new)
```

## After upgrading

- `python -m pytest -q` should report **382 passed**.
- Restart the Studio so the server picks up the changed modules.
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.20-1`). If anything still looks stale, hard-refresh with
  Ctrl+Shift+R.
- The system tab's `torch.compile` card now fully prefers the last
  autotune probe: "available · triton x.y.z" when the probe proved it,
  "not available · <reason>" when it proved a kernel-build failure, and
  the static import check only as fallback.
- No data re-processing is needed. Existing datasets and checkpoints stay
  valid.
