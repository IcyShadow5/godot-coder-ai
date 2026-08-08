$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw ".venv was not found. Create the virtual environment first and install the project."
}

# Single instance: refuse to start a second Studio on the same port.
$listening = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host "[Godot Coder Studio] A Studio instance already appears to be running on port 8765."
    Write-Host "Close the other Studio window first, or start it with a different --port."
    exit 1
}

& $python -m godot_coder.studio
