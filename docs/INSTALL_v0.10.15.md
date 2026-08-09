# Installing / upgrading to v0.10.15

v0.10.15 is the second batch of robustness fixes from the external review.
Fast imports no longer leak temp working copies, the GitHub archive path
got the same zip-bomb guard as local uploads, instruction data uses every
function in a file, audits resume from their checkpoint after a crash, and
checkpoints load with torch's safe unpickler. Progress events can no longer
be silently dropped by a 0 index.

## Fresh install (from source)

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python.exe -m godot_coder.doctor
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m godot_coder.studio
```

## Upgrading an existing install

Replace these files from the release zip (or apply the packaged upgrade
script):

```
docs/CHANGELOG_v0.10.15.md
docs/INSTALL_v0.10.15.md
CHANGELOG.md
README.md
.gitignore                       # runtime-noise rules
src/godot_coder/checkpoint.py    # weights_only=True + RNG state as primitives
src/godot_coder/corpus.py        # zip-bomb preflight moved here
src/godot_coder/corpus_audit.py  # checkpoint resume
src/godot_coder/instruction_data.py  # per-file task cap fixed
src/godot_coder/local_sources.py # workspace cleanup + is-not-None guards
src/godot_coder/studio.py        # direct .project import
src/godot_coder/ui/paths.py      # dropped re-export
tests/test_checkpoint.py         # +4 weights_only tests
tests/test_golden_tasks.py       # real signature check
tests/test_instruction_data.py   # +1 multi-function test
tests/test_local_sources.py      # +1 workspace-leak + +1 zero-index test
tests/test_path_security_gaps.py # +2 zip-bomb tests
tests/test_professional_core.py  # +1 audit-resume test
src/godot_coder/ui/static/sw.js  # cache bump
src/godot_coder/__init__.py      # version marker
pyproject.toml                   # version (optional)
```

## After upgrading

- `python -m pytest -q` should report **263 passed**.
- Restart the Studio so the server picks up the changed modules.
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.15-1`). If anything still looks stale, hard-refresh with
  Ctrl+Shift+R.
- No data re-processing is needed. Existing datasets and checkpoints stay
  valid - legacy checkpoints load through the numpy allowlist, new ones
  store their RNG state primitively.
