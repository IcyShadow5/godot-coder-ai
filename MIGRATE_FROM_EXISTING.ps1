param(
    [string]$ExistingProject = ""
)

$ErrorActionPreference = "Stop"
$NewProject = (Resolve-Path $PSScriptRoot).Path
if (-not $ExistingProject) {
    $ExistingProject = Read-Host "Path to the previous Godot Coder installation"
}
$OldProject = (Resolve-Path $ExistingProject).Path
if ($OldProject -eq $NewProject) {
    throw "Source and target are identical. Use the clean upgrade package for this case."
}
$OldPython = Join-Path $OldProject ".venv\Scripts\python.exe"
if (-not (Test-Path $OldPython)) {
    throw "No reusable .venv found: $OldPython"
}
if (Test-Path (Join-Path $NewProject ".venv")) {
    throw "A .venv already exists in the new folder. Remove it first or use the in-place upgrade."
}

Write-Host "Linking the existing Python/CUDA runtime ..."
New-Item -ItemType Junction -Path (Join-Path $NewProject ".venv") -Target (Join-Path $OldProject ".venv") | Out-Null

foreach ($name in @("checkpoints", "reports")) {
    $source = Join-Path $OldProject $name
    $target = Join-Path $NewProject $name
    if ((Test-Path $source) -and -not (Test-Path $target)) {
        New-Item -ItemType Junction -Path $target -Target $source | Out-Null
    }
}

$oldCorpus = Join-Path $OldProject "data\corpus"
$newCorpus = Join-Path $NewProject "data\corpus"
if ((Test-Path $oldCorpus) -and -not (Test-Path $newCorpus)) {
    New-Item -ItemType Junction -Path $newCorpus -Target $oldCorpus | Out-Null
}

foreach ($relative in @("artifacts", "data\raw", "data\processed", ".studio_backups")) {
    $source = Join-Path $OldProject $relative
    $target = Join-Path $NewProject $relative
    if (Test-Path $source) {
        New-Item -ItemType Directory -Path $target -Force | Out-Null
        & robocopy $source $target /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Copy failed: $relative (robocopy $LASTEXITCODE)" }
    }
}

Push-Location $NewProject
try {
    & $OldPython -m pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) { throw "Project installation failed." }
    & $OldPython -m godot_coder.doctor
    if ($LASTEXITCODE -ne 0) { Write-Warning "Doctor reported a runtime problem. Details are above." }
    & $OldPython -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed." }
} finally {
    Pop-Location
}

Write-Host "Migration completed."
Write-Host "Important: .venv and some large folders are linked to the old project. Do not delete the old folder."
