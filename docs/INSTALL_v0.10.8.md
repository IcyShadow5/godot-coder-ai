# Installing / upgrading to v0.10.8

v0.10.8 adds AMD ROCm (HIP) support alongside CUDA and MPS, and fixes the
prepare_data "finished but frozen" hang (it no longer dumps the 10MB manifest
to stdout, so tokenization jobs exit cleanly).

## Fresh install (from source)

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python.exe -m godot_coder.doctor
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m godot_coder.studio
```

## Upgrading an existing install (from v0.10.7)

Copy the changed files from the release into your install, then restart the
Studio:

```
src/godot_coder/prepare_data.py     # no more 10MB manifest dump on stdout
src/godot_coder/runtime.py          # ROCm detection + device: rocm
src/godot_coder/ui/services.py      # system status reports ROCm
src/godot_coder/ui/static/app.js    # Studio panel shows ROCm ready
src/godot_coder/ui/static/sw.js     # service-worker cache bump
src/godot_coder/doctor.py           # pytorch_hip_build line
tests/test_runtime_device.py        # ROCm device tests (optional)
tests/test_data.py                  # prepare_data clean-exit regression (optional)
src/godot_coder/__init__.py         # version marker
pyproject.toml                      # version + metadata (optional)
README.md / CHANGELOG.md / docs/     # docs (optional)
```

Or, on a release distribution, the packaged upgrade script (`APPLY_*.ps1`)
handles the file list for you.

## After upgrading

- `python -m pytest -q` should report **193 passed**.
- On AMD hardware, the Studio system panel should say **ROCm ready** (and
  `device: rocm` works in configs); on Apple Silicon it says **MPS ready**;
  on NVIDIA it says **CUDA ready**.
- Re-run `prepare_data` if you need to regenerate a token stream: it now
  exits cleanly instead of lingering after writing the output.
- The service worker will refresh the Studio UI once (cache bumped to
  `v0.10.8-1`).
