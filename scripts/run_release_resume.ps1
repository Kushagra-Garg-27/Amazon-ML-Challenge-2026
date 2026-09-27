$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = 'code/business_entity_resolution/src'
$env:PYTHONUTF8 = '1'
$env:ER_FEATURE_DUCKDB_MEMORY_MB = '1200'
$env:ER_TEST_FEATURE_TMP_DIR = 'work/test_features/tmp_resume4'
"$(Get-Date -AsUTC -Format o) detached resume from features" | Add-Content -LiteralPath work/test_release_driver.log
& .venv\Scripts\python.exe -m er.test_pipeline.release --resume-from features *>> work/test_release_driver_resume3.log
$releaseExitCode = $LASTEXITCODE
$releaseExitCode | Set-Content -LiteralPath work/test_release_driver_resume3_exit_code.txt
exit $releaseExitCode
