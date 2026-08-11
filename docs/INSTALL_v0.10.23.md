# Installing / upgrading to v0.10.23

v0.10.23 is the streaming release: chat tokens arrive live instead of as
one finished block, and the training workspace shows the loss curve while
the run is still going. On top of that it pays down the deep-review
backlog (central sampling defaults, eval-cache cleanup, real validate
timing, snapshot throttling, bounded process output) and makes
`start_studio.bat`/`start_studio.ps1` work from a naked checkout. 564 tests.

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
docs/CHANGELOG_v0.10.23.md
docs/INSTALL_v0.10.23.md
CHANGELOG.md
README.md
mkdocs.yml                          # nav -> v0.10.23 docs
pyproject.toml                      # version bump
src/godot_coder/__init__.py         # version marker
src/godot_coder/ui/static/sw.js     # cache bump
src/godot_coder/sampling.py         # new: central sampling defaults
src/godot_coder/model.py            # generate_stream (KV cache) + generate wrapper
src/godot_coder/ui/services.py      # streaming service + real validate timing
src/godot_coder/ui/routers/chat.py  # SSE worker thread + disconnect handling
src/godot_coder/ui/schemas.py       # defaults read from sampling.py
src/godot_coder/autotune.py         # measured tokens_per_step in recommendation
src/godot_coder/process_control.py  # bounded output queue
src/godot_coder/ui/jobs.py          # snapshot throttling + live loss history
src/godot_coder/progress_events.py  # loss/learning_rate numeric event fields
src/godot_coder/train.py            # train_loss events + eval-cache cleanup
src/godot_coder/corpus_audit.py     # English thousands separators
src/godot_coder/ui/static/stream.js # new: SSE parser (tested in Node)
src/godot_coder/ui/static/app.js    # streaming caret, done event, loss sparkline
src/godot_coder/ui/static/index.html  # live loss block
src/godot_coder/ui/static/styles.css  # caret + loss chart styles
start_studio.bat                    # python fallback + PYTHONPATH
start_studio.ps1                    # python fallback + PYTHONPATH
tests/test_sampling.py              # new: central sampling defaults
tests/test_model.py                 # extended: stream identity on both paths
tests/test_ui_services.py           # extended: streaming service events
tests/test_ui_server_stream.py      # extended: SSE endpoint + disconnect
tests/test_ui_jobs.py               # extended: loss history ring + bounds
tests/test_train.py                 # extended: emitter round-trip, eval-cache
tests/js_stream_test.cjs            # new: SSE parser in Node
tests/test_ui_progress_assets.py    # extended: asset + node hooks
tests/test_scale_plan.py            # extended: tokens_per_step
tests/test_autotune.py              # extended: recommendation fields
tests/test_generate.py              # extended: sampling defaults
tests/test_professional_core.py     # extended: preflight separators
tests/test_full_audit_v078.py       # extended: separators
```

## After upgrading

- `python -m pytest -q` should report **564 passed**.
- Restart the Studio so the server picks up the changed modules.
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.23-1`). If anything still looks stale, hard-refresh with
  Ctrl+Shift+R.
- The chat streams tokens now: the completion grows live under a blinking
  caret, and the final text is the cleaned version (repetition loops
  collapsed).
- The training workspace shows a live loss value plus sparkline while a
  run is active — no polling changes needed, the data rides in the
  existing job snapshot.
- No data re-processing is needed. Existing datasets and checkpoints stay
  valid.
