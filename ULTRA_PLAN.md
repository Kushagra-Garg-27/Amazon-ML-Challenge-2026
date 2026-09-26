# ULTRA_PLAN.md — Phased Execution (Phases 0–18)

Each phase: **Objective · Files · In→Out · Algorithm · Experiments · Metrics · Accept · Fail ·
Deps · Runtime · Rollback**. Metrics are measured and logged to the ledger — never invented.
Global budget: ~0.8GB RAM (out-of-core mandatory), deterministic seeds, UTF-8 I/O.

## Phase 0 — Audit & environment  ✅ DONE
- Objective: know data, env, tools before coding. Files: `scripts/profile_dataset.py`, `CLAUDE.md`.
- In→Out: raw TSVs → confirmed profile + venv. Algorithm: streaming histogram profiler.
- Experiments: profile all 6 sources + GT. Metrics: row/country/length/match-count stats.
- Accept: profile reproduced, venv imports OK. Fail: missing files / import error.
- Deps: none. Runtime: mins. Rollback: n/a.

## Phase 1 — Ingestion & integer-coding
- Objective: convert TSVs to compact columnar store with int-coded IDs (memory).
- Files: `src/er/io.py`, `scripts/ingest.py`. In→Out: `dataset/**.tsv` → `work/*.parquet` +
  `id_maps` (entity_id↔int32, per source). 
- Algorithm: DuckDB `read_csv(delim='\t')` → write parquet; build id maps; keep raw strings
  only in parquet (not RAM). Country kept as string label (open set).
- Experiments: verify row counts equal profile; roundtrip a sample id.
- Metrics: rows written, file sizes, peak RAM. Accept: counts match §profile exactly.
- Fail: count mismatch, dtype overflow. Deps: P0. Runtime: mins. Rollback: delete `work/`.

## Phase 2 — Normalization
- Objective: deterministic multi-key normalization (PRD §16). Files: `src/er/normalize.py`,
  `tests/test_normalize.py`.
- In→Out: name/address strings → `{name_norm,name_nosuffix,name_sorted,name_acronym,addr_norm,
  num_tokens}`. Algorithm: NFKC→casefold→dejunk→`&`→and→accent-fold→suffix-canon→(translit key).
- Experiments: unit tests on curated variant pairs (Pvt/Private, Corp/Corporation, `&`/and,
  accented FR, Devanagari). Metrics: % test cases passing, collision sanity.
- Accept: all unit tests green; no crash on full data sample. Fail: rule regressions.
- Deps: P1. Runtime: secs (tests). Rollback: revert module.

## Phase 3 — GT analysis, assumption checks & validation split
- Objective: validate PRD assumptions; build the scoring split. Files: `src/er/analyze_gt.py`,
  `src/er/split.py`, `work/val_split.parquet`.
- In→Out: GT + sources → EDA report + stratified holdout. Algorithm: join GT to source rows
  (DuckDB); measure within-country match rate, cross-script (Devanagari↔Latin) rate, both-source
  & within-source-multi shares; stratify holdout by country×match-count bucket.
- Experiments: Q1 within-country %, Q2 cross-script %, Q3 score-separability peek.
- Metrics: those %s + split sizes. Accept: report produced, split reproducible (seeded).
- Fail: join blows memory (→ batch by country). Deps: P1–P2. Runtime: 10–30 min. Rollback: redo split.

## Phase 4 — Blocking passes
- Objective: implement high-recall candidate keys (PRD §17). Files: `src/er/blocking.py`.
- In→Out: normalized sources → per-pass (S1_int, cand_int) candidate edges. Algorithm: P1 exact
  nosuffix key; P2 sorted-token key; P3 rare-token inverted index (DF-thresholded); P4 char
  3–4gram TF-IDF top-k cosine (sparse, batched); P5 address numeric/PIN + name-token co-block.
  All partitioned by country via DuckDB.
- Experiments: per-pass recall & size on val split. Metrics: recall, avg size, runtime, RAM.
- Accept: each pass runs out-of-core within RAM; per-pass recall logged. Fail: OOM / pass with
  ~0 recall. Deps: P1–P3. Runtime: 20–60 min/pass. Rollback: disable a pass.

## Phase 5 — Union, prune & candidate set
- Objective: produce the final model-input candidate set. Files: `src/er/candidates.py`.
- In→Out: per-pass edges → deduped union, per-S1 cap K by cheap score → `work/candidates.parquet`
  → later `candidate_pairs.tsv`. Algorithm: union distinct; cheap prefilter (token Jaccard /
  n-gram cosine); cap top-K; keep provenance (passes, rank).
- Experiments: sweep K & prune thresholds vs recall/size. Metrics: recall, mean/median/p90/p95/
  p99/max size, reduction ratio, zero-candidate count, true pairs lost.
- Accept: recall–size knee chosen with evidence. Fail: recall below target at feasible size.
- Deps: P4. Runtime: 20–40 min. Rollback: change K/threshold.

## Phase 6 — Blocking evaluation
- Objective: quantify blocking quality (separately ranked). Files: `src/er/eval_blocking.py`.
- In→Out: candidates + GT → recall/reduction/size report + Pareto curve. Algorithm: set ops on
  edges vs GT pairs, per country. Experiments: recall vs mean-size Pareto; error-analyze lost
  pairs (script? empty addr? rare tokens?). Metrics: as §18. Accept: report + Pareto committed;
  lost-pair reasons categorized. Fail: unexplained recall gap. Deps: P5. Runtime: mins. Rollback: n/a.

## Phase 7 — Feature engineering
- Objective: pairwise feature matrix (PRD §19). Files: `src/er/features.py`.
- In→Out: candidate pairs + normalized fields → float32 feature table (parquet, batched).
  Algorithm: rapidfuzz name/addr similarities, sparse TF-IDF cosines, set overlaps, provenance,
  missing/country flags. Country-agnostic. Experiments: feature/label correlation, leakage check.
- Metrics: #features, gen runtime, RAM, NaN rate. Accept: reproducible matrix, no leakage.
- Fail: OOM (→ batch), constant/NaN features. Deps: P5. Runtime: 30–90 min. Rollback: drop features.

## Phase 8 — Training-set assembly & hard negatives
- Objective: labeled pairs for the model. Files: `src/er/dataset.py`.
- In→Out: candidates+GT+features (train split) → train/valid matrices with labels. Algorithm:
  positives = GT pairs present in candidates; negatives = other candidates; hard negatives =
  high-similarity non-matches; balance by subsampling easy negatives. Experiments: neg-ratio &
  hard-neg mix sweeps. Metrics: pos/neg counts, class ratio. Accept: no S1 leakage across splits.
- Fail: positive coverage < blocking recall (bug). Deps: P6–P7. Runtime: 15–30 min. Rollback: reassemble.

## Phase 9 — Model comparison & selection
- Objective: pick the scorer empirically (PRD §20). Files: `src/er/train.py`, `src/er/models.py`.
- In→Out: train matrices → trained candidate models + comparison table. Algorithm: fit logistic
  (baseline), LightGBM, XGBoost with early stopping; identical features/splits. Experiments:
  head-to-head on val macro-F_0.5 (with a provisional τ), PR-AUC, runtime, RAM. Metrics: those.
- Accept: winner beats logistic baseline on macro-F_0.5 with a real run id. Fail: no model beats
  baseline (→ revisit features). Deps: P8. Runtime: 20–60 min. Rollback: keep baseline.

## Phase 10 — Probability calibration
- Objective: reliable probabilities for thresholding. Files: `src/er/calibrate.py`.
- In→Out: winner scores → calibrated scores. Algorithm: isotonic/Platt on held-out. Experiments:
  reliability curve, Brier before/after. Metrics: Brier, ECE. Accept: calibration improves or
  neutral, τ tuning stabilized. Fail: worse calibration. Deps: P9. Runtime: mins. Rollback: skip.

## Phase 11 — Threshold optimization
- Objective: τ maximizing macro-F_0.5. Files: `src/er/threshold.py`. In→Out: calibrated val
  scores → τ (global; optional per-country / per-match-count). Algorithm: sweep τ∈[0,1], compute
  macro-F_0.5 incl. singletons; test conditional τ only if it beats global. Experiments: global vs
  per-country vs per-bucket. Metrics: macro-F_0.5, P, R at chosen τ. Accept: τ chosen on val, not
  LB. Fail: unstable τ across folds. Deps: P10. Runtime: mins. Rollback: revert to global τ.

## Phase 12 — Singleton handling
- Objective: protect the 5.6% singletons (each worth 1.0). Files: `src/er/singleton.py`.
- In→Out: scores → optional has-match gate. Algorithm: measure singleton P/R at τ; optionally a
  precision-first gate for entities whose best candidate is weak. Experiments: with/without gate.
- Metrics: singleton P/R, macro-F_0.5 delta. Accept: no macro-F_0.5 regression. Fail: gate hurts
  multi-match recall. Deps: P11. Runtime: mins. Rollback: disable gate.

## Phase 13 — Multi-match assembly & decision policy
- Objective: assemble per-S1 predicted sets (dominant case). Files: `src/er/predict.py`.
- In→Out: scored candidates → per-S1 matched-id list. Algorithm: keep all cand with p≥τ; optional
  match-count-prior cap (≤11). Experiments: threshold-only vs +cap. Metrics: macro-F_0.5, both-
  source & within-source-multi recovery. Accept: beats threshold-only or neutral. Fail: cap hurts.
- Deps: P11–P12. Runtime: mins. Rollback: threshold-only.

## Phase 14 — Cross-script India enhancement
- Objective: recover Devanagari↔Latin pairs (if Phase 3 shows they matter). Files: extend
  `normalize.py`/`blocking.py`/`features.py`. Algorithm: transliteration key + script-robust
  features + address anchors. Experiments: India recall/F_0.5 with vs without. Metrics: India-only
  blocking recall & macro-F_0.5. Accept: India metrics improve, no global regression. Fail: noisy
  translit hurts precision. Deps: P3–P13. Runtime: 20–40 min. Rollback: drop translit key.

## Phase 15 — France zero-shot / generalization
- Objective: ensure transfer to unseen FR. Files: `src/er/loco.py`. Algorithm: leave-one-country-
  out probes (train US→score India, etc.); verify no {US,India} hard-coding; extend suffix/street
  dicts to FR tokens (generic, not entity lookup). Experiments: LOCO macro-F_0.5. Metrics: per-
  country F_0.5, LOCO gap. Accept: LOCO within acceptable gap; FR path runs end-to-end. Fail: large
  LOCO collapse (→ more country-agnostic features). Deps: P13. Runtime: 20–40 min. Rollback: n/a.

## Phase 16 — Full-scale test inference
- Objective: run the pipeline on the full test set out-of-core. Files: `scripts/run_test.py`.
- In→Out: test sources → scored candidates. Algorithm: normalize→block→features→score in country/
  id batches; checkpoint per batch. Experiments: dry-run on a shard first. Metrics: wall-clock,
  peak RAM, #candidates. Accept: completes within RAM, every test S1 covered. Fail: OOM/timeout
  (→ smaller batches). Deps: P4–P15. Runtime: 1–3 h. Rollback: resume from checkpoint.

## Phase 17 — Output generation, validation & error analysis
- Objective: produce & gate the two TSVs. Files: `src/er/submit.py`. Algorithm: write
  `matching_results.tsv` + `candidate_pairs.tsv` with exact format (no stray spaces; matches ⊆
  candidates; every S1 one row; singleton=empty); run `utils/validate_submission.py`. Experiments:
  final val error analysis (FP/FN by country/script/size). Metrics: validator PASS, val macro-F_0.5.
- Accept: `PASS` printed; matches⊆candidates. Fail: any validator issue. Deps: P16. Runtime: mins.
  Rollback: fix formatting, re-emit.

## Phase 18 — Packaging
- Objective: graded zip. Files: `code/business_entity_resolution/{src,README.md,requirements.txt}`,
  filled `Documentation_template.md`, `output/*.tsv`. Algorithm: assemble structure, pin versions,
  write reproduction README, fill methodology (candidate gen, features, model, results with run
  ids). Experiments: clean-clone reproduction dry-run. Metrics: zip builds, re-run regenerates
  outputs. Accept: outputs reproducible from `code/` alone; docs complete. Fail: missing dep/file.
- Deps: P17. Runtime: mins. Rollback: rebuild zip.
