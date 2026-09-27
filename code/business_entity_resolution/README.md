# broCode business entity resolution release

## Environment and data

Use **Python 3.12.10** on Windows, with at least 48 GB free scratch disk and 1.35 GB free RAM before each large stage. The observed process peak in the held-out run was under 1.5 GB, but a full test run may need more scratch. The five direct Python dependencies are pinned in `requirements.txt` (NumPy, DuckDB, PyArrow, RapidFuzz, LightGBM); LightGBM's SciPy and Narwhals transitive dependencies are pinned in `constraints.txt`. From the archive root:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -c "import sys; assert sys.version_info[:3] == (3, 12, 10), sys.version"
.venv\Scripts\python.exe -m pip install -r code\business_entity_resolution\requirements.txt -c code\business_entity_resolution\constraints.txt
$env:PYTHONPATH='code/business_entity_resolution/src'
$env:PYTHONUTF8='1'
New-Item -ItemType Directory -Force work,output | Out-Null
Copy-Item code\business_entity_resolution\release\* work\
```

Place the supplied challenge files, unmodified, under:

```text
dataset/train/train_source1.tsv, train_source2.tsv, train_source3.tsv, train_ground_truth.tsv
dataset/test/test_source1.tsv, test_source2.tsv, test_source3.tsv
```

The packaged frozen model and configurations are sufficient for test inference; retraining is unnecessary. No test labels or external identity service is used. The final output is `output/matching_results.tsv` and `output/candidate_pairs.tsv`.

## Release stages and resume

From the archive root, run the stages in this order, one large data process at a time:

```powershell
.venv\Scripts\python.exe -m er.test_pipeline.ingest
.venv\Scripts\python.exe -m er.test_pipeline.candidates prepare
.venv\Scripts\python.exe -m er.test_pipeline.release_smoke
.venv\Scripts\python.exe -m er.test_pipeline.candidates run
.venv\Scripts\python.exe -m er.test_pipeline.candidate_report
.venv\Scripts\python.exe -m er.test_pipeline.features
.venv\Scripts\python.exe -m er.test_pipeline.score
.venv\Scripts\python.exe -m er.test_pipeline.assemble
.venv\Scripts\python.exe -m er.test_pipeline.audit
.venv\Scripts\python.exe -m er.test_pipeline.diagnostics
```

After candidates finish, `python -m er.test_pipeline.release` runs the remaining audit, feature, scoring, assembly, diagnostics and validator stages sequentially. Use `--resume-from features` (or any listed stage) after an interruption; each stage also validates and reuses its own completed parts.

All data stages use atomic writes and SHA-256 receipts. Re-run the same command to resume; valid completed parts are reused. To isolate a failed partition, use `--only p00_france` on candidate, feature, score and assembly modules. The candidate policy has 16 logical hash buckets, each split into two execution chunks, for 96 country/hash work units. Countries are discovered from input keys; France uses the same frozen policy. DuckDB uses approximately 700 MB internal memory and a single thread. Model scoring reads 200,000 candidates per batch as float32. Approximate test runtime from the held-out projection is 9.6 hours plus ingestion, rank construction and retries; actual runtime varies with hardware and France density.

Do not edit `work/final_candidate_policy.json`, `work/feature_spec_v1_1.json`, `work/final_matcher_model.txt`, or `work/final_matcher_policy.json`. Their hashes are verified by the stage code.

## Output validation

The matching file has the exact header `source1_entity_id<TAB>matched_entity_ids`; the candidate file has `source1_entity_id<TAB>candidate_entity_ids`. Both have one row per raw test S1. A blank list is encoded as an explicit empty second field after a tab. Candidate lists contain all final post-pruning pairs scored by the model, including pairs below the decision threshold. Match lists contain all pairs scoring at least 0.61.

```powershell
.venv\Scripts\python.exe -m er.test_pipeline.official_validator --matching output\matching_results.tsv --candidate output\candidate_pairs.tsv --test-dir dataset\test
.venv\Scripts\python.exe -m er.test_pipeline.official_validator --matching output\matching_results.tsv --candidate output\candidate_pairs.tsv --test-dir dataset\test --check-ids
```

`er.test_pipeline.official_validator_original` preserves the byte-identical challenge helper (`SHA-256 f96f59934383a15095914f507620c078c474e8f9c60e864b2d051ed173a22dfc`). The callable `official_validator` module keeps the challenge checks and CLI while streaming files larger than 256 MB, since the original helper stores all candidate lists in RAM. The ID-check mode holds the test S2/S3 ID set in memory; run it only when sufficient free RAM is available. The independent release audit separately streams both outputs and compares them against candidate and score partitions.

## Troubleshooting

If a partition stops, keep completed receipts and restart the same command. Remove only an incomplete `.partial.parquet` or `.partial.tsv` after confirming no process writes it. If DuckDB reports out-of-memory, lower concurrent load, ensure scratch space, and retry a smaller physical partition while preserving the 50/50 quotas, DF threshold 2,000, heavy-block cap 100, feature definitions, model, and threshold. A checksum mismatch requires investigating source/config changes before proceeding. The output validator must report PASS before submission.
