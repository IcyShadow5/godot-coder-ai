# Installing / upgrading to v0.10.22

v0.10.22 is the chat-quality sampling release: the sampler now has a
repetition penalty (Studio default 1.15, plus a slider) and optional nucleus
(`top_p`) sampling, and the chat pipeline collapses repeated blocks in
completions. Measured on the golden suite, parser pass rate goes from
2/30 (6.7 %) to 6/30 (20 %) with the new defaults - no retraining involved.
Also fixes a latent checkpoint-resume crash on GPU (RNG state) that would
have broken any continue-training run. 564 tests.

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
docs/CHANGELOG_v0.10.22.md
docs/INSTALL_v0.10.22.md
CHANGELOG.md
README.md
mkdocs.yml                          # nav -> v0.10.22 docs
pyproject.toml                      # version bump
src/godot_coder/__init__.py         # version marker
src/godot_coder/ui/static/sw.js     # cache bump
src/godot_coder/model.py            # repetition penalty + top_p
src/godot_coder/ui/services.py      # chat defaults + completion cleanup
src/godot_coder/ui/routers/chat.py  # pass top_p / repetition_penalty through
src/godot_coder/ui/schemas.py       # GenerateRequest top_p + repetition_penalty
src/godot_coder/generate.py         # --top-p / --repetition-penalty
src/godot_coder/benchmark.py        # --top-p / --repetition-penalty
src/godot_coder/checkpoint.py       # RNG restore fix for GPU resume
src/godot_coder/ui/static/index.html  # repetition penalty slider
src/godot_coder/ui/static/app.js    # slider wiring
tests/test_chat_sampling.py         # new: cleanup + penalty tests
tests/test_model.py                 # extended: penalty/top_p/loop-break
tests/test_generate.py              # extended: CLI defaults
tests/test_checkpoint.py            # extended: RNG restore regression tests
tests/test_ui_route_gaps.py         # extended: router pass-through
tests/test_ui_server_stream.py      # extended: stream fake signature
```

## After upgrading

- `python -m pytest -q` should report **564 passed**.
- Restart the Studio so the server picks up the changed modules.
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.22-1`). If anything still looks stale, hard-refresh with
  Ctrl+Shift+R.
- The chat now has a "Repetition penalty" slider (default 1.15) - leave it
  on unless you want raw completions.
- No data re-processing is needed. Existing datasets and checkpoints stay
  valid.
