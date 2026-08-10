# Installing / upgrading to v0.10.17

v0.10.17 adds optional `torch.compile` support (wired up per platform via a
new `compile` extra - works on Windows now thanks to the community
`triton-windows` wheel), hardens the autotuner against compile probes that
fail without reporting `status: error`, and fixes the benchmark harness
that was grading bare completions instead of the full scaffold + completion.

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

Replace these files from the release zip (or apply the packaged upgrade
script):

```
docs/CHANGELOG_v0.10.17.md
docs/INSTALL_v0.10.17.md
CHANGELOG.md
README.md
mkdocs.yml                      # nav -> v0.10.17 docs
.coderabbit.yaml                # CodeRabbit review config (repo only)
pyproject.toml                  # version + new compile extra
src/godot_coder/autotune.py     # compile-failure detection (not just status=error)
src/godot_coder/benchmark.py    # validate prompt + completion (scaffold re-attached)
tests/test_autotune.py          # +5 compile-path tests
tests/test_benchmark.py         # +1 scaffold regression test
src/godot_coder/ui/static/sw.js # cache bump
src/godot_coder/__init__.py     # version marker
```

## After upgrading

- `python -m pytest -q` should report **345 passed**.
- Restart the Studio so the server picks up the changed modules.
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.17-1`). If anything still looks stale, hard-refresh with
  Ctrl+Shift+R.
- The new `compile` extra is optional. Skip it and everything behaves
  exactly as before - the autotuner probe will simply report
  `compile_available: false`. Install it with
  `pip install -e ".[compile]"` and the trainer and autotuner will use
  `torch.compile` automatically on the next run.
- No data re-processing is needed. Existing datasets and checkpoints stay
  valid.
