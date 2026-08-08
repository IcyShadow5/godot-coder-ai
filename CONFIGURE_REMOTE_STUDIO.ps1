$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw ".venv was not found. Run the regular installation or the upgrade first."
}
Write-Host "Setting up Secure Remote Studio for Tailscale Serve."
Write-Host "The Studio itself stays bound to 127.0.0.1:8765."
& $python -m godot_coder.remote_access --root $PSScriptRoot configure --port 8765
if ($LASTEXITCODE -ne 0) { throw "Remote setup failed." }
Write-Host "Now start start_studio.ps1 and open the displayed Tailscale HTTPS address on your phone."
