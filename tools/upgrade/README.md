# Upgrade templates

This folder contains **templates** for building a versioned upgrade package for a
future release. It deliberately contains **no built upgrader**: no `payload/`,
no real file lists, no versioned APPLY script. A ready-made package is assembled
from these templates per release and distributed with the release (e.g. in the
release workspace); the repo itself stays free of build output.

## When to use this

A new release changed files that a live install should receive. Build an
upgrade package that copies exactly those files and nothing else.

## How to build an upgrade package

1. Copy `template/APPLY_TEMPLATE.ps1` to e.g. `APPLY_V0110_UPGRADE.ps1`,
   `template/APPLY_TEMPLATE.bat` to `APPLY_V0110_UPGRADE.bat`, and
   `template/build_TEMPLATE_payload.ps1` to `build_V0110_payload.ps1`.
2. In both copies set `$UpgradeVersion = "v0.11.0"` and fill `$files` with the
   relative path of every file that changed in this release (same list in both).
3. From a repo checkout run `build_V0110_payload.ps1` — it assembles `payload/`
   from the listed files (gitignored build output).
4. Hand the folder to the live install and run:
   `APPLY_V0110_UPGRADE.ps1 -ExistingProject "C:\path\to\CodingAi"`
   or double-click `APPLY_V0110_UPGRADE.bat` and paste the project path.
5. The apply script backs up every replaced file to
   `.upgrade_backups\v0.11.0_<timestamp>` and then runs `doctor` + `pytest -q`
   automatically. It never touches `.venv`, `data`, `checkpoints`, `artifacts`,
   `reports`, `.studio_backups`, `.upgrade_backups` or `configs/autotuned_*.yaml`.

## Layout

- `template/APPLY_TEMPLATE.ps1` — versioned apply script (backup → copy → verify)
- `template/APPLY_TEMPLATE.bat` — double-click wrapper that asks for the project path
- `template/build_TEMPLATE_payload.ps1` — payload assembler
