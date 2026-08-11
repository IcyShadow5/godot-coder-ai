# Installing / upgrading to v0.10.24

v0.10.24 is the quality release: the chat gets a Stop button and the
"generation stopped" badge, conversations are persistent sessions, and
the deep-review backlog is fully closed (helper-file leak, path
traversal, secret masking, GET side effects, verify hardening). The
license is now Apache-2.0 with NOTICE for the separately-released
weights. 694 tests.

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
docs/CHANGELOG_v0.10.24.md
docs/INSTALL_v0.10.24.md
CHANGELOG.md
README.md
mkdocs.yml                          # nav -> v0.10.24 docs
pyproject.toml                      # version bump
src/godot_coder/__init__.py         # version marker
src/godot_coder/ui/static/sw.js     # cache bump
src/godot_coder/ui/services.py      # stop endpoint wiring, configs no longer write on GET
src/godot_coder/ui/routers/chat.py  # /api/chat/stop + session persistence + cancelled flag
src/godot_coder/chat_store.py       # new: persistent chat sessions
src/godot_coder/ui/static/app.js    # Stop button, cancelled badge, session list, context panel
src/godot_coder/ui/static/index.html  # stop button + session list
src/godot_coder/ui/static/styles.css  # stop/cancelled + session styles
src/godot_coder/corpus.py           # helper cleanup on every exit path, original_path guard
src/godot_coder/verify.py           # read-only falsification mode, cache, retry cleanup
src/godot_coder/train.py            # task checkpoint + resume state
src/godot_coder/checkpoint.py       # tokenizer drift warning
src/godot_coder/tokenizer.py        # versioned builds
src/godot_coder/provenance.py       # new: context provenance + head-preserving truncation
src/godot_coder/progress_events.py  # broader secret masking
start_studio.bat                    # dependency check before launch
start_studio.ps1                    # dependency check before launch
```

## After upgrading

- `python -m pytest -q` should report **694 passed**.
- Restart the Studio so the server picks up the changed modules.
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.24-1`). If anything still looks stale, hard-refresh with
  Ctrl+Shift+R.
- The chat now shows a Stop button while a generation runs; stopping
  marks the answer with a "generation stopped" badge instead of showing
  a truncated completion as if it were finished.
- Conversations are kept as persistent sessions - switch and delete them
  from the session list.
- No data re-processing is needed. Existing datasets and checkpoints stay
  valid.
