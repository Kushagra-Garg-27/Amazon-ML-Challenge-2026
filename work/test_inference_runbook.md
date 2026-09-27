# Frozen test inference runbook

The test-specific pipeline is implemented under `code/business_entity_resolution/src/er/test_pipeline/`. This runbook records the execution path; the release README has the reproducible setup and full command reference. The candidate policy, feature specification, model, normalization, and threshold remain frozen. The release gate is PASS.

## Inputs and stage order

The exact frozen input checksums are recorded in `work/test_release_preflight.json` and `Documentation_template.md`. The release requirements and allowed package structure are recorded in `work/release_requirements_audit.json`. Test normalization and its independent audit are complete. The deterministic sample smoke gate passed and is recorded in `work/test_smoke/smoke_result.json`.

Target document frequencies and a 32-way physical hash selection have been built. The 16 logical policy buckets are subdivided into 32 physical work chunks; each country/chunk pair yields 96 restartable units. Country is handled as an open-set value, so France uses the same frozen policy.

From the repository root, with the verified virtual environment:

```powershell
$env:PYTHONPATH='code/business_entity_resolution/src'
$env:PYTHONUTF8='1'
.venv\Scripts\python.exe -m er.test_pipeline.candidates run
.venv\Scripts\python.exe -m er.test_pipeline.release
.venv\Scripts\python.exe scripts/build_submission.py
```

The candidate command verifies existing checksummed receipts on restart. The release driver runs candidate structural audit, feature materialization, scoring, TSV assembly, independent output audit, label-free diagnostics, and both validator modes, one large process at a time. To restart after a failed release-driver stage, run `python -m er.test_pipeline.release --resume-from STAGE`, where `STAGE` is the failed stage from the release log. Keep completed receipts and fix only execution mechanics.

## Resource and integrity gates

Candidate generation uses a 700 MB DuckDB internal limit and one thread. The release driver requires at least 1.35 GB free RAM and 48 GB free disk before every stage, and feature materialization uses a 900 MB DuckDB limit. The active candidate run is monitored in `work/test_candidate_resource_samples.csv` and `work/test_candidate_temp_samples.csv`. Stop on free RAM below 0.5 GB. Each part is atomically written with a SHA-256 receipt. No raw data, labels, policy edits, retraining, or threshold edits are permitted during test inference.

The output audit independently checks exact headers, one row per raw S1, explicit empty fields, valid target identity, complete scored-candidate identity, frozen threshold decisions, subset relation, and file hashes. The validator output and ID-check output are captured under `work/official_validator*.log/json`. The original challenge validator is preserved as `er.test_pipeline.official_validator_original`; a memory-bounded streaming extension handles the full candidate file using the same CLI and checks.

The final submission is `broCode_submission.zip`, with team members Kushagra Garg and Tanisha Mandavia. The archive builder stages an allowlist, repeats output checks, extracts the ZIP, checks member hashes, imports packaged entry points, and reruns the validator on extracted outputs.
