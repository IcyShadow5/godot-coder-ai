$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $python = $venvPython
} else {
    $python = "python"
    $env:PYTHONPATH = Join-Path $PSScriptRoot "src"
    Write-Host "[Godot Coder Studio] .venv not found - using system python with PYTHONPATH=src"
}

# Single instance: refuse to start a second Studio on the same port.
$listening = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host "[Godot Coder Studio] A Studio instance already appears to be running on port 8765."
    Write-Host "Close the other Studio window first, or start it with a different --port."
    exit 1
}

& $python -m godot_coder.studio
