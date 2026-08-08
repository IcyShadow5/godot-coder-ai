# Installation and upgrade to v0.7.9

> **Superseded / Archive.** This guide applies to v0.7.9 and describes the
> old `APPLY_CUMULATIVE_V079_UPGRADE.bat` flow. The current guide is in
> `docs/INSTALL_v0.10.2.md`, the upgrade package in `upgrade/`.

# Installation and upgrade to v0.7.9

## Clean ZIP

The clean ZIP contains only project code, configurations, tests, documentation and start scripts. It contains no models, checkpoints, private sources, reports, data folders or virtual environment.

For a new installation follow the regular Python/CUDA setup and then run in the project folder:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

## Upgrading an existing installation

1. Stop running training, download, audit or corpus jobs and close the Studio.
2. Unpack the upgrader ZIP into a new folder.
3. Start `APPLY_CUMULATIVE_V079_UPGRADE.bat`.
4. Select the existing Godot-Coder-AI folder.
5. After a successful test run start the Studio again.

The upgrader only overwrites the files from the package. It never replaces or deletes `.venv`, `data`, `checkpoints`, `artifacts`, `reports`, `.studio_backups` or earlier `.upgrade_backups`.

Before every overwrite a copy of the actually replaced files is created under `<Project>\.upgrade_backups\v0.7.9-<timestamp>`. In case of a failed test run this backup is kept; there is no automatic rollback.

## After the upgrade

```powershell
.\.venv\Scripts\python.exe -m godot_coder.doctor
.\.venv\Scripts\python.exe -m pytest -q
.\start_studio.bat
```

`doctor` additionally checks Godot and the CUDA runtime. On a machine without a correctly installed Godot or CUDA path this check can fail even though the pure Python tests passed.
