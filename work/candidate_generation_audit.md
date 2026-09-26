# Candidate-generation audit trail

Reproduce:

    PYTHONUTF8=1 .venv/Scripts/python scripts/measure_peak.py --script scripts/candidate_experiments.py -- --data-dir dataset

Bounded DuckDB (memory_limit=1GB, threads=4, temp=work/duckdb_tmp; spills to disk). Measured process peak RSS ~1.27 GiB via scripts/measure_peak.py (GetProcessMemoryInfo PeakWorkingSetSize), well under available RAM. Recall measured exactly on the GT-pair oracle; single-key volume via key products; token-pass volume via materialized per-S1-capped distinct sets.
