param(
    [string]$InstallPath = (Get-Location).Path,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $InstallPath).Path.TrimEnd('\')
$normalizedRoot = $root.ToLowerInvariant()
$patterns = @(
    ($normalizedRoot + '\data\corpus\downloads\local-'),
    ($normalizedRoot + '\reports\local_sources\validation_work\')
)

function Get-Descendants([int]$ParentPid) {
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $ParentPid")
    foreach ($child in $children) {
        Get-Descendants -ParentPid ([int]$child.ProcessId)
        $child
    }
}

$matches = @(Get-CimInstance Win32_Process | Where-Object {
    $name = [string]$_.Name
    $command = ([string]$_.CommandLine).ToLowerInvariant()
    if ($name -notmatch '^godot.*\.exe$' -or [string]::IsNullOrWhiteSpace($command)) { return $false }
    if ($command -notlike '*--path*') { return $false }
    foreach ($pattern in $patterns) {
        if ($command.Contains($pattern)) { return $true }
    }
    return $false
})

if ($matches.Count -eq 0) {
    Write-Host "No leftover Godot Coder validation found."
    exit 0
}

Write-Host "Found Godot Coder validation processes:"
$matches | ForEach-Object { Write-Host ("- PID {0}: {1}" -f $_.ProcessId, $_.CommandLine) }

if (-not $Force) {
    $answer = Read-Host "Terminate only these listed process trees? [Y/N]"
    if ($answer -notin @('J','j','Y','y')) {
        Write-Host "Cancelled."
        exit 2
    }
}

$errors = @()
foreach ($process in $matches) {
    try {
        $descendants = @(Get-Descendants -ParentPid ([int]$process.ProcessId))
        foreach ($child in $descendants) {
            Stop-Process -Id ([int]$child.ProcessId) -Force -ErrorAction SilentlyContinue
        }
        Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction Stop
        Write-Host ("Terminated: PID {0}" -f $process.ProcessId)
    } catch {
        $errors += ("PID {0}: {1}" -f $process.ProcessId, $_.Exception.Message)
    }
}

if ($errors.Count -gt 0) {
    Write-Error ("Not all processes could be terminated:`n" + ($errors -join "`n"))
    exit 1
}
Write-Host "Validation processes were cleaned up. Other Godot processes were left untouched."
exit 0
