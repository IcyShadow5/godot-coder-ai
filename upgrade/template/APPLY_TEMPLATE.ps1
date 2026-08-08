# Godot Coder AI – Upgrade Template (apply side)
# ---------------------------------------------------------------------------
# TEMPLATE — copy to APPLY_V<NEW>_UPGRADE.ps1 for a new release, then:
#   1. Set $UpgradeVersion to the release (e.g. "v0.11.0").
#   2. Replace $files with the exact relative paths changed in this release.
#   3. Build payload/ next to this script via build_TEMPLATE_payload.ps1.
#   4. Run: APPLY_V<NEW>_UPGRADE.ps1 -ExistingProject "C:\path\to\CodingAi"
# ---------------------------------------------------------------------------
param(
    [Parameter(Mandatory = $true)]
    [string]$ExistingProject,
    [switch]$ConfirmStopped
)

$ErrorActionPreference = "Stop"
$UpgradeVersion = "vX.Y.Z"
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$PayloadRoot = Join-Path $ScriptDir "payload"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Godot Coder AI $UpgradeVersion - Upgrade" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Validate target -------------------------------------------------
$Target = (Resolve-Path $ExistingProject -ErrorAction Stop).Path
$TargetSrc = Join-Path $Target "src\godot_coder"
if (-not (Test-Path (Join-Path $TargetSrc "local_sources.py"))) {
    Write-Host "ERROR: $TargetSrc\local_sources.py not found." -ForegroundColor Red
    Write-Host "Make sure -ExistingProject points to the root of an installed godot-coder-ai project." -ForegroundColor Red
    exit 1
}

# Replace this list with every file that changed in this release:
$files = @(
    # "src\godot_coder\example.py",
    # "docs\CHANGELOG_v0.11.0.md",
)

# --- Confirm ----------------------------------------------------------
if (-not $ConfirmStopped) {
    Write-Host "Target : $Target" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Files that will be upgraded ($($files.Count) total):" -ForegroundColor White
    foreach ($f in $files) {
        Write-Host "  $f" -ForegroundColor Green
    }
    Write-Host ""
    Write-Host 'The Studio must be STOPPED before applying.' -ForegroundColor Yellow
    $answer = Read-Host "Type JA to proceed"
    if ($answer -ne "JA") {
        Write-Host "Aborted." -ForegroundColor Red
        exit 0
    }
}

# --- Backup -----------------------------------------------------------
$BackupRoot = Join-Path $Target ".upgrade_backups"
$BackupDir  = Join-Path $BackupRoot "$($UpgradeVersion)_$(Get-Date -Format 'yyyyMMdd-HHmmss')"
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

foreach ($f in $files) {
    $srcPath = Join-Path $Target $f
    if (Test-Path $srcPath) {
        $destPath = Join-Path $BackupDir $f
        New-Item -ItemType Directory -Force -Path (Split-Path $destPath) | Out-Null
        Copy-Item $srcPath $destPath
        Write-Host "Backed up: $f" -ForegroundColor Gray
    }
}

# --- Apply ------------------------------------------------------------
foreach ($f in $files) {
    $payloadPath = Join-Path $PayloadRoot $f
    if (-not (Test-Path $payloadPath)) {
        Write-Host "WARNING: payload file missing (skipped): $f" -ForegroundColor Yellow
        continue
    }
    $targetPath = Join-Path $Target $f
    New-Item -ItemType Directory -Force -Path (Split-Path $targetPath) | Out-Null
    Copy-Item $payloadPath $targetPath -Force
    Write-Host "Upgraded: $f" -ForegroundColor Green
}

Write-Host ""
Write-Host "$UpgradeVersion upgrade applied. Backup: $BackupDir" -ForegroundColor Green
Write-Host ""

# --- Post-upgrade verification (non-fatal) ----------------------------
$VenvPython = Join-Path $Target ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    Write-Host 'Running godot_coder.doctor ...' -ForegroundColor Cyan
    & $VenvPython -m godot_coder.doctor
    $DoctorExit = $LASTEXITCODE
    Write-Host ""
    Write-Host 'Running pytest -q ...' -ForegroundColor Cyan
    & $VenvPython -m pytest -q
    $PytestExit = $LASTEXITCODE
    Write-Host ""
    if ($DoctorExit -ne 0 -or $PytestExit -ne 0) {
        Write-Host 'VERIFICATION FAILED (doctor or pytest). Restore from the backup above.' -ForegroundColor Red
        Write-Host "  Backup: $BackupDir" -ForegroundColor Gray
        exit 1
    }
    Write-Host 'Verification finished - doctor and pytest passed.' -ForegroundColor Green
} else {
    Write-Host 'No .venv found at target - skipping doctor/pytest. Run them manually:' -ForegroundColor Yellow
    Write-Host '  .\.venv\Scripts\python.exe -m godot_coder.doctor' -ForegroundColor Yellow
    Write-Host '  .\.venv\Scripts\python.exe -m pytest -q' -ForegroundColor Yellow
}
