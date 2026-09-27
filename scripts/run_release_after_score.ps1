$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = 'code/business_entity_resolution/src'
$env:PYTHONUTF8 = '1'
"$([DateTime]::UtcNow.ToString('o')) detached resume from assemble" | Add-Content -LiteralPath work/test_release_driver.log
& .venv\Scripts\python.exe -m er.test_pipeline.release --resume-from assemble *>> work/test_release_driver_resume4.log
$releaseExitCode = $LASTEXITCODE
$releaseExitCode | Set-Content -LiteralPath work/test_release_driver_resume4_exit_code.txt
exit $releaseExitCode
