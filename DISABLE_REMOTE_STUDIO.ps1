$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw ".venv was not found." }
& $python -m godot_coder.remote_access --root $PSScriptRoot disable --reset-serve
if ($LASTEXITCODE -ne 0) { throw "Remote deactivation failed." }
