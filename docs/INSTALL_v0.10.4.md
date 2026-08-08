# Installing / upgrading to v0.10.4

No new configuration options in this one - it is validation correctness,
cosmetics and version metadata only. Still worth the apply: the classifier
now really catches hard syntax errors instead of keeping them as warnings.

## If you are on v0.10.3 (recommended path)

Apply the `godot-coder-ai-v0.10.4-upgrade` package:

```powershell
# from the package folder:
.\APPLY_V0104_UPGRADE.ps1 -ExistingProject "C:\path\to\CodingAi"
```

or double-click `APPLY_V0104_UPGRADE.bat` and enter the project path.

The upgrader backs up every replaced file to
`.upgrade_backups/v0.10.4-<timestamp>`.

## After applying

1. Stop any running Studio/validate processes first - the upgrader does not
   check for running processes, and a running Studio keeps the old code in
   memory until restart.
2. Verify the version:
   `python -c "import godot_coder; print(godot_coder.__version__)"` -> `0.10.4`.
3. Re-run `validate` once. The validation cache was bumped to v3, so every
   project is re-checked with the corrected classifier; records with real
   syntax errors are now excluded instead of kept as warnings.
4. Optionally run the test suite: `python -m pytest -q`.

## What changed

See `docs/CHANGELOG_v0.10.4.md`.
