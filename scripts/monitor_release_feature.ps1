param([int]$TargetPid)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
while ($true) {
    $target = Get-Process -Id $TargetPid -ErrorAction SilentlyContinue
    if (-not $target) { break }
    $timestamp = Get-Date -AsUTC -Format o
    $freeRam = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory * 1024
    $freeDisk = (Get-PSDrive -Name C).Free
    Add-Content -LiteralPath work/test_feature_system_samples.csv -Value "$timestamp,$($target.WorkingSet64),$($target.PeakWorkingSet64),$freeRam,$freeDisk"
    Start-Sleep -Seconds 10
}
