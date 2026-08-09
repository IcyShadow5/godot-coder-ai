# Installation and upgrade to v0.10.3

Current guide (replaces `docs/archive/INSTALL_v0.7.9.md`).

## Fresh install

The clean ZIP (or the repo checkout) contains only project code, configurations,
tests, documentation and start scripts - no models, checkpoints, private
sources, reports, data folders or virtual environment.

For a new installation follow the regular Python/CUDA setup and then run in the
project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m godot_coder.doctor
.\.venv\Scripts\python.exe -m pytest -q
```

## Upgrading an existing installation (v0.10.2 -> v0.10.3)

1. Stop running training, download, audit or corpus jobs and close the Studio.
2. Use the finished upgrade package (v0.10.3) - it is shipped separately with the release and contains `APPLY_V0103_UPGRADE.ps1` (or `.bat`) plus `payload/`. The repo itself keeps only **templates** in `upgrade/` (see `upgrade/README.md`) to build its own package for future releases.
3. If the package does not contain a `payload/` yet: run the included `build_v0103_payload.ps1` (builds the payload from the current repo state).
4. Start `APPLY_V0103_UPGRADE.bat` and specify the path to the existing Godot-Coder-AI folder
   (or directly: `APPLY_V0103_UPGRADE.ps1 -ExistingProject "C:\...\CodingAi"`).
5. After a successful test run (doctor + pytest run automatically) start the Studio again.

The upgrader only overwrites the files from the package. It never replaces or deletes
`.venv`, `data`, `checkpoints`, `artifacts`, `reports`, `.studio_backups`,
`.upgrade_backups` or `configs/autotuned_*.yaml`.

Before every overwrite a copy of the actually replaced files is created under
`<Project>\.upgrade_backups\v0.10.3-<timestamp>`. In case of a
failed test run this backup is kept; there is no
automatic rollback - manually: copy the backup files back.

## After the upgrade

```powershell
.\.venv\Scripts\python.exe -m godot_coder.doctor
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m godot_coder.studio
```

`doctor` also checks Godot and the CUDA runtime. On a machine without
a correctly installed Godot or CUDA path this check can fail even though
the pure Python tests passed.

## What changed since v0.10.2

See `docs/CHANGELOG_v0.10.3.md`. In short: The corpus validation now runs
via the managed-process runner (job objects kill the complete
process tree) - large Mono projects can no longer hang the validate step.
The Studio JobManager also terminates jobs that deliver no output for
longer than `GODOT_CODER_JOB_STALL_TIMEOUT_SECONDS` (default 20 minutes, `0` disables)
and marks them as failed instead of endlessly as
"running". In addition a missing `import os` in `validate_dataset.py`
was fixed (latent `NameError`).
