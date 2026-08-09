# Installing / upgrading to v0.10.13

v0.10.13 is a training-core correctness release. The deep code review
finally sat down with model.py and train.py and found four real bugs: the
causal mask could silently turn off during chunked prefill (wrong tokens
during generation), sliding-window evaluation ignored the configured batch
size, tied embeddings were getting weight decay, and the data prefetcher
pinned GPU memory on the main thread. All four are fixed. This release also
finishes the documentation pass - every module, class and function now has
a docstring, and the JS files have section headers.

## Fresh install (from source)

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python.exe -m godot_coder.doctor
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m godot_coder.studio
```

## Upgrading an existing install (from v0.10.12)

Copy the changed files from the release into your install, then restart
the Studio:

```
CHANGELOG.md                        # v0.10.13 entry
docs/CHANGELOG_v0.10.13.md          # new patch notes
docs/INSTALL_v0.10.13.md            # this file
src/godot_coder/model.py            # causal masking + layer comments
src/godot_coder/train.py            # sliding eval, optimizer groups, prefetch
src/godot_coder/autotune.py         # docstrings
src/godot_coder/data.py             # TokenStream docstring
src/godot_coder/checkpoint.py       # module docstring
src/godot_coder/curriculum.py       # module docstring
src/godot_coder/generate.py         # module docstring
src/godot_coder/evaluate.py         # module docstring
src/godot_coder/config.py           # module docstring
src/godot_coder/tokenizer.py        # module docstring
src/godot_coder/prepare_data.py     # module docstring
src/godot_coder/instruction_data.py # module docstring
src/godot_coder/godot_cli.py        # module docstring
src/godot_coder/ui/static/api.js    # section headers
src/godot_coder/ui/static/remote.js # section headers
src/godot_coder/ui/static/sw.js     # cache bump
src/godot_coder/__init__.py         # version marker
pyproject.toml                      # version (optional)
```

Or, on a release distribution, the packaged upgrade script
(`APPLY_*.ps1`) handles the file list for you.

## After upgrading

- `python -m pytest -q` should report **251 passed**.
- Restart the Studio so the server picks up the changed modules.
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.13-1`). If anything still looks stale, hard-refresh with
  Ctrl+Shift+R.
- No data re-processing is needed - these are training-loop and doc
  changes, not dataset changes. Existing checkpoints stay valid; the next
  training run simply gets the corrected mask, eval batching, optimizer
  groups and prefetching.
