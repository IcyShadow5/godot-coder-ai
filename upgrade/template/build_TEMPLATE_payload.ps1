# Godot Coder AI – Upgrade Template (payload builder)
# ---------------------------------------------------------------------------
# TEMPLATE — copy to build_V<NEW>_payload.ps1 for a new release.
# Assembles upgrade\payload\ from the current repo checkout. Keep the $files
# list identical to the matching APPLY_V<NEW>_UPGRADE.ps1.
# ---------------------------------------------------------------------------
$ErrorActionPreference = "Stop"
$UpgradeVersion = "vX.Y.Z"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent (Split-Path -Parent $ScriptDir)  # upgrade\template -> repo root
$Payload   = Join-Path (Split-Path -Parent $ScriptDir) "payload"

# Replace this list with the same relative paths as the APPLY script:
$files = @(
    # "src\godot_coder\example.py",
    # "docs\CHANGELOG_v0.11.0.md",
)

Write-Host "Building $UpgradeVersion payload into: $Payload" -ForegroundColor Cyan
if (Test-Path $Payload) {
    Remove-Item $Payload -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Payload | Out-Null

foreach ($f in $files) {
    $srcPath = Join-Path $RepoRoot $f
    if (-not (Test-Path $srcPath)) {
        Write-Host "MISSING in repo (skipped): $f" -ForegroundColor Yellow
        continue
    }
    $destPath = Join-Path $Payload $f
    New-Item -ItemType Directory -Force -Path (Split-Path $destPath) | Out-Null
    Copy-Item $srcPath $destPath
    Write-Host "  payload: $f" -ForegroundColor Green
}

Write-Host ""
Write-Host "Done. Now copy the upgrade folder to the target machine and run the APPLY script." -ForegroundColor Green
