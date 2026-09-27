$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = 'code/business_entity_resolution/src'
$env:PYTHONUTF8 = '1'
$env:ER_FEATURE_DUCKDB_MEMORY_MB = '1200'
$env:ER_TEST_FEATURE_TMP_DIR = 'work/test_features/tmp_resume2'
& .venv\Scripts\python.exe -m er.test_pipeline.features --only p01_france *> work/test_feature_probe.log
$probeExitCode = $LASTEXITCODE
$probeExitCode | Set-Content -LiteralPath work/test_feature_probe_exit_code.txt
exit $probeExitCode
