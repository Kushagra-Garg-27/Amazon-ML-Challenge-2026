# IMPLEMENTATION_PLAN.md — Task Backlog (ER-###)

Executable tasks mapped to `ULTRA_PLAN.md` phases. Fields: **Priority · Deps · Files · Detail ·
Test · Validate · Output**. P0=blocker/critical-path, P1=important, P2=enhancement. Every task
logs a run to the experiment ledger (ER-028). No metric is invented — it comes from a run.

Legend: paths under `code/business_entity_resolution/` unless noted. CLI = the `er` dispatcher (ER-029).

---

**ER-001 · P0 · Deps: — · Files: `src/er/io.py`, `scripts/ingest.py`**
Detail: DuckDB `read_csv(delim='\t', header=true)` each source → `work/{split}_s{n}.parquet`;
build int32 id maps (entity_id↔code) per source, persisted. Keep strings in parquet, not RAM.
Test: assert parquet row counts == profiled counts; random id roundtrip. Validate: counts exact.
Output: `work/*.parquet`, `work/idmap_*.parquet`.

**ER-002 · P0 · Deps: ER-001 · Files: `src/er/io.py`**
Detail: streaming/batched readers (polars `scan_parquet`, country-partition iterators) + UTF-8
stdout helper. Test: iterate a shard within RAM budget. Validate: peak RAM logged < budget.
Output: reusable IO API.

**ER-003 · P0 · Deps: ER-001 · Files: `src/er/normalize.py`, `src/er/resources/suffixes.py`**
Detail: implement PRD §16 keys (name_norm, name_nosuffix, name_sorted, name_acronym, addr_norm,
num_tokens) + multi-locale legal-suffix/street dict (US/India/FR, generic). Deterministic, stdlib.
Test: see ER-004. Validate: no crash over a 100k-row sample. Output: normalization API.

**ER-004 · P0 · Deps: ER-003 · Files: `tests/test_normalize.py`**
Detail: curated variant pairs (Pvt/Private, Corp/Corporation, Ltd/Limited, `&`/and, accented FR,
Devanagari, leading junk, domain-as-name). Test: pytest asserts equal keys for variants. Validate:
100% target cases pass. Output: green test suite.

**ER-005 · P0 · Deps: ER-001 · Files: `src/er/analyze_gt.py`**
Detail: DuckDB-join GT to source rows (batched by country); measure within-country match rate,
Devanagari↔Latin cross-script rate, both-source & within-source-multi shares, score-separability
peek. Test: totals reconcile to profiled 7,638,365 matches. Validate: report reproducible.
Output: `work/gt_analysis.md` + numbers to ledger.

**ER-006 · P0 · Deps: ER-005 · Files: `src/er/split.py`**
Detail: stratified holdout by country×match-count bucket (seeded). Test: split sizes stable across
runs. Validate: no S1 overlap train/val. Output: `work/val_split.parquet`.

**ER-007 · P0 · Deps: ER-006 · Files: `src/er/evaluate.py`**
Detail: macro-F_0.5 (per-S1, incl. singletons) + P/R + per-country + singleton breakdown from a
predictions file vs GT file. Test: hand-checked tiny fixture (e.g., the spec's 0.714 example).
Validate: matches manual F_0.5. Output: `er evaluate` command + scores.

**ER-008 · P0 · Deps: ER-003,ER-006 · Files: `src/er/blocking.py` (pass P1)**
Detail: exact `name_nosuffix` key blocks, per country, via DuckDB. Test: recall/size on val.
Validate: runs out-of-core. Output: pass-1 candidate edges.

**ER-009 · P0 · Deps: ER-008 · Files: `src/er/blocking.py` (pass P2)**
Detail: sorted-token key blocks. Test: recall/size on val. Validate: within RAM. Output: pass-2 edges.

**ER-010 · P0 · Deps: ER-008 · Files: `src/er/blocking.py` (pass P3)**
Detail: rare-token inverted index (DF-thresholded co-occurrence). Test: recall/size; DF cutoff
sweep. Validate: within RAM. Output: pass-3 edges.

**ER-011 · P0 · Deps: ER-008 · Files: `src/er/blocking.py` (pass P4)**
Detail: char 3–4gram TF-IDF (sklearn `HashingVectorizer`/`TfidfVectorizer`, sparse) top-k cosine
per S1, batched per country. Test: recall/size, k sweep. Validate: sparse mats fit RAM (batched).
Output: pass-4 edges.

**ER-012 · P1 · Deps: ER-003,ER-006 · Files: `src/er/blocking.py` (pass P5)**
Detail: address numeric/PIN + name-token co-block. Test: recall lift on entities with addresses.
Validate: within RAM. Output: pass-5 edges.

**ER-013 · P0 · Deps: ER-008..ER-012 · Files: `src/er/candidates.py`**
Detail: union distinct edges; cheap prefilter score; per-S1 cap top-K; keep provenance (passes,
rank). Test: K & threshold sweep vs recall/size. Validate: matches ⊆ candidates invariant holds
downstream. Output: `work/candidates_{split}.parquet`.

**ER-014 · P0 · Deps: ER-013 · Files: `src/er/submit.py` (candidate writer)**
Detail: write `candidate_pairs.tsv` exact format (headers, TABs, one row/S1, empty when none, no
stray spaces). Test: validator candidate checks. Validate: `validate_submission.py` candidate rules
pass. Output: `output/candidate_pairs.tsv`.

**ER-015 · P0 · Deps: ER-013 · Files: `src/er/eval_blocking.py`**
Detail: candidate recall, reduction ratio, size distribution (mean/median/p90/p95/p99/max), zero-
candidate count, lost-pair list + reasons. Test: recall == |kept∩GT|/|GT|. Validate: Pareto curve
saved. Output: `work/blocking_report.md`, ledger metrics.

**ER-016 · P0 · Deps: ER-013 · Files: `src/er/features.py`**
Detail: PRD §19 pairwise features (rapidfuzz, sparse cosines, overlaps, provenance, flags), float32,
batched to parquet. Test: leakage check, NaN rate. Validate: reproducible matrix. Output:
`work/features_{split}.parquet`.

**ER-017 · P0 · Deps: ER-015,ER-016 · Files: `src/er/dataset.py`**
Detail: positives = GT∩candidates; negatives = other candidates; hard negatives = high-sim non-
matches; subsample easy negs. Test: positive coverage == blocking recall. Validate: no S1 leakage
across splits. Output: `work/train_matrix.parquet`, `work/valid_matrix.parquet`.

**ER-018 · P0 · Deps: ER-017 · Files: `src/er/train.py`, `src/er/models.py`**
Detail: fit logistic (baseline), LightGBM, XGBoost; identical splits; early stopping; comparison
table. Test: winner > baseline macro-F_0.5 (provisional τ). Validate: seeds fixed, run logged.
Output: `work/model_*.txt/json` + comparison to ledger.

**ER-019 · P1 · Deps: ER-018 · Files: `src/er/calibrate.py`**
Detail: isotonic/Platt calibration on held-out. Test: Brier before/after, reliability curve.
Validate: neutral-or-better. Output: calibrator artifact.

**ER-020 · P0 · Deps: ER-018(,ER-019) · Files: `src/er/threshold.py`**
Detail: sweep τ maximizing macro-F_0.5 (incl. singletons); global; test per-country/per-bucket.
Test: chosen τ reproduces reported val F_0.5. Validate: tuned on val only. Output: `work/threshold.json`.

**ER-021 · P1 · Deps: ER-020 · Files: `src/er/singleton.py`**
Detail: measure singleton P/R; optional precision-first has-match gate. Test: with/without gate
macro-F_0.5. Validate: no regression. Output: gate config + metrics.

**ER-022 · P0 · Deps: ER-020(,ER-021) · Files: `src/er/predict.py`**
Detail: assemble per-S1 sets (p≥τ, optional ≤11 cap). Test: both-source & within-source-multi
recovery. Validate: macro-F_0.5 ≥ threshold-only. Output: `work/predictions_{split}.parquet`.

**ER-023 · P1 · Deps: ER-005,ER-022 · Files: extend normalize/blocking/features**
Detail: if ER-005 shows material cross-script, add transliteration key + script-robust features +
address anchors. Test: India-only recall/F_0.5 with vs without. Validate: no global regression.
Output: India metrics to ledger.

**ER-024 · P1 · Deps: ER-022 · Files: `src/er/loco.py`**
Detail: leave-one-country-out probes; assert no {US,India} hard-coding; extend FR suffix/street
tokens (generic). Test: LOCO macro-F_0.5. Validate: FR path runs end-to-end. Output: LOCO report.

**ER-025 · P0 · Deps: ER-022 · Files: `scripts/run_test.py`**
Detail: full test pipeline out-of-core, checkpointed per country/id batch. Test: shard dry-run
first. Validate: completes within RAM; every test S1 covered. Output: scored test candidates.

**ER-026 · P0 · Deps: ER-025,ER-014,ER-007 · Files: `src/er/submit.py`**
Detail: write both TSVs exact-format; enforce matches ⊆ candidates; run `utils/validate_submission.py`.
Test: validator PASS. Validate: exit 0 `PASS`. Output: `output/matching_results.tsv`,
`output/candidate_pairs.tsv`.

**ER-027 · P1 · Deps: ER-022 · Files: `src/er/error_analysis.py`**
Detail: FP/FN breakdown by country/script/match-count/empty-addr on val. Test: reconciles to
macro-F_0.5. Validate: actionable failure buckets. Output: `work/error_analysis.md`.

**ER-028 · P0 · Deps: — · Files: `src/er/ledger.py`**
Detail: append-only experiment ledger (DuckDB table/JSONL): run id, config, code hash, metrics,
runtime, peak RAM. Test: write+read a run. Validate: every experiment cites a run id. Output:
`work/ledger.duckdb`.

**ER-029 · P0 · Deps: — · Files: `src/er/cli.py`, `src/er/__main__.py`**
Detail: `er` dispatcher: `ingest|normalize|analyze-gt|split|block|candidates|features|dataset|
train|calibrate|threshold|predict|evaluate|submit|validate|run-test`. Test: `er --help` lists all.
Validate: each subcommand runs. Output: single reproducible entry point.

**ER-030 · P0 · Deps: ER-026 · Files: `code/.../README.md`, `requirements.txt`**
Detail: pin versions (numpy 2.5.3, pandas 3.0.6, scipy 1.18.1, scikit-learn 1.9.1, rapidfuzz
3.14.6, lightgbm 4.7.0, xgboost 3.4.1, polars 1.44.2, pyarrow 25.0.1, duckdb 1.5.5); write end-to-
end run instructions. Test: fresh-venv install. Validate: instructions reproduce outputs. Output:
runnable package.

**ER-031 · P0 · Deps: ER-026,ER-027 · Files: `Documentation_template.md`**
Detail: fill methodology (EDA, blocking strategy + recall/size, features, model comparison,
results with run ids, error analysis). Test: all template sections filled. Validate: claims cite
ledger runs. Output: completed methodology doc.

**ER-032 · P0 · Deps: ER-030,ER-031 · Files: `scripts/build_submission.py`**
Detail: assemble `<team>_submission.zip` (output/, code/, Documentation). Clean-clone reproduction
dry-run. Test: zip builds; re-run regenerates both TSVs. Validate: outputs reproducible from code/
alone. Output: submission zip.
