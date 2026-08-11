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

# Dependency check: fail with a clear message instead of a traceback.
# The Python code is single-quoted on purpose - double quotes inside a -c
# argument get mangled by Windows PowerShell 5.1 when re-quoting it for
# the native call.
$missing = & $python -c "import importlib.util, sys; missing = [m for m in ('fastapi', 'torch') if importlib.util.find_spec(m) is None]; sys.exit('missing packages: ' + ', '.join(missing)) if missing else None" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[Godot Coder Studio] Required packages are missing in the selected Python: $missing"
    Write-Host 'Install them with:  .venv\Scripts\pip install -e ".[dev]"'
    exit 1
}

# Single instance: refuse to start a second Studio on the same port.
$listening = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host "[Godot Coder Studio] A Studio instance already appears to be running on port 8765."
    Write-Host "Close the other Studio window first, or start it with a different --port."
    exit 1
}

& $python -m godot_coder.studio
