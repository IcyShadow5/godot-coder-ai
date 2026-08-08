# Installing / upgrading to v0.10.5

v0.10.5 closes the last validation gap: context-warning records are now
additionally parsed per file, so nothing is kept unverified.

## If you are on v0.10.3 (recommended path)

The `godot-coder-ai-v0.10.5-upgrade` package is cumulative from v0.10.3 -
it includes every v0.10.4 and v0.10.5 change, so one apply is enough:

```powershell
# from the package folder:
.\APPLY_V0105_UPGRADE.ps1 -ExistingProject "C:\path\to\CodingAi"
```

or double-click `APPLY_V0105_UPGRADE.bat` and enter the project path.

The upgrader backs up every replaced file to
`.upgrade_backups/v0.10.5-<timestamp>`.

## After applying

1. Stop any running Studio/validate processes first - the upgrader does not
   check for running processes, and a running Studio keeps the old code in
   memory until restart.
2. Verify the version:
   `python -c "import godot_coder; print(godot_coder.__version__)"` -> `0.10.5`.
3. Re-run `validate` once. The validation cache is v4, so every project is
   re-checked with the complete pipeline: hard syntax errors, strict-project
   checker, and per-file verification of context warnings.
4. Optional: `python -m pytest -q`.

## What changed

See `docs/CHANGELOG_v0.10.5.md` and `docs/CHANGELOG_v0.10.4.md`.
