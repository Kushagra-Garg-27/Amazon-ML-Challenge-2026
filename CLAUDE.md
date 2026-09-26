# CLAUDE.md — Operating Manual (Business Entity Resolution Challenge)

> Read this first every session. It encodes confirmed facts, hard rules, and gotchas so
> future sessions don't re-derive them or break the submission. Authoritative spec:
> `ProblemStatement.txt` / `CHALLENGE.md`. Detailed plans: `PRD.md`, `ULTRA_PLAN.md`,
> `IMPLEMENTATION_PLAN.md`.

## What this project is
Entity Resolution across 3 noisy sources. **Source 1 is the deduplicated reference.** For
every S1 entity, find all matching S2 and/or S3 records (zero, one, or many). Two outputs:
`output/matching_results.tsv` (final matches, the leaderboard file) and
`output/candidate_pairs.tsv` (the exact candidate set fed to the model at inference — the
last blocking stage). **Every matched ID must also appear in candidate_pairs.**

## Metric — optimize this, not intuition
`F_0.5 = (1.25·P·R) / (0.25·P + R)`, computed **per S1 entity, then macro-averaged** over
all S1. Precision-heavy: a false merge hurts ~2× a miss. A correct singleton (empty
prediction) scores **1.0**; any predicted match on a true singleton scores **0.0**.

## 🚫 FAIR PLAY — DISQUALIFYING IF VIOLATED
STRICTLY NO external business-identity lookup or enrichment. Do NOT use Google/Maps,
commercial ER APIs, government/business registries, geocoding APIs, external company
datasets, or any internet-based entity enrichment. Learn ONLY from the provided data.
Web access is allowed solely for generic technical docs — NEVER to resolve/enrich a
business. Transliteration/normalization via deterministic local algorithms (stdlib
`unicodedata`) is fine; looking up a real business is not.

## Confirmed dataset facts (profiled — do NOT re-profile or invent)
- Rows: train S1 **2,206,821** / S2 **5,034,616** / S3 **5,285,603**;
  test S1 **1,732,544** / S2 **4,887,273** / S3 **5,082,316**.
- Countries: train = {US, India}; test adds **France (15% of test S1, zero training rows)**.
  Treat country as an **open set** — never hard-code/one-hot {US, India}.
- Ground truth: avg **3.46 matches/S1**; dist `{0:5.6%,1:5.4%,2:17%,3:24%,4:22%,5+:26%}`;
  max 11. **80.5%** match both an S2 and an S3; **76.8%** match multiple within one source.
  → one-to-**many** is the norm; top-1 selection is wrong; use per-candidate thresholding.
- Noise: Devanagari↔Latin (India), accents (France), legal-suffix variants
  (Pvt/Private, Ltd/Limited, Corp/Corporation, Sarl/SCI), `&`↔`and`, word-order swaps,
  typos, leading junk (`--`, `<<`), domains-as-names, ~3% empty addresses (S2/S3).
- Integrity (verified `scripts/integrity_check.py`, 2026-09-25): entity_id unique within
  every file; S2/S3 id namespaces disjoint; **every GT id resolves in-source** (0 missing);
  0 duplicate S1 GT rows / 0 intra-list dup ids; **each S2/S3 matches ≤1 S1 (cross-S1
  sharing = 0)** → splitting on S1 partitions labels with no target leakage. Names never
  empty (0); empty addresses S2≈3.36%/S3≈3.33%. GT has one row per train S1 (singletons
  5.6%). Baseline exact (country,name_nosuffix) block: **val true-pair recall only ~47%**
  (US 49%, India 44%) — exact-name blocking is insufficient; needs fuzzy/token + translit.

## Environment
- Interpreter: `.venv/Scripts/python` (Python **3.12**; system 3.14 lacks wheels — do not use it).
- Stack (all permissive licenses): numpy 2.5.3, pandas 3.0.6, scipy 1.18.1,
  scikit-learn 1.9.1, rapidfuzz 3.14.6, lightgbm 4.7.0, xgboost 3.4.1, polars 1.44.2,
  pyarrow 25.0.1, duckdb 1.5.5.
- **Memory: ~0.8 GB free RAM** is the dominant constraint. MANDATE out-of-core design:
  DuckDB for big joins (spills to disk), polars streaming/lazy, integer-coded IDs,
  scipy sparse matrices, batched inference. NEVER `pd.read_csv` a full 5M-row string table.
- Windows/UTF-8: every script must `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`
  and read files with `encoding="utf-8"`. Run with `PYTHONUTF8=1`.

## Submission format — validator gotchas (breaking these = rejection)
- Headers exactly: `source1_entity_id\tmatched_entity_ids` (candidates:
  `source1_entity_id\tcandidate_entity_ids`). Real TABS, not commas.
- One row per test S1 entity; **every** test S1 must appear; singleton = empty field.
- **The S1 id is NOT stripped by the validator** → emit no leading/trailing spaces.
  **IDs are NOT stripped** → no spaces after commas. Format: `S1-0001\tS2-0007,S3-0012`.
- Only S2-/S3- IDs in lists; no self-matches to S1-; no duplicate IDs in a list; no
  duplicate S1 rows; no IDs absent from the test set.
- Gate: `.venv/Scripts/python utils/validate_submission.py --matching output/matching_results.tsv --candidate output/candidate_pairs.tsv --test-dir dataset/test` must print `PASS`.

## Model constraints
Final model MIT/Apache-2.0, ≤8B params. Tabular GBM (LightGBM/XGBoost) is the plan — well
within. **Do NOT default to an LLM.** Compare models empirically before choosing.

## Working rules (competitive mode)
- **Measure, never assume.** Every proposed improvement: baseline → experiment → report
  F_0.5 / precision / recall / blocking-recall / reduction-ratio / candidate-size / runtime →
  keep only if the evidence supports it. Log every run to the experiment ledger.
- **Do not invent** dataset facts, benchmark numbers, or "X is better" claims without a run.
- Blocking is separately ranked: **smaller candidate sets per S1 rank higher.** Track the
  recall/size trade-off explicitly; don't inflate candidates.
- Don't overfit the public leaderboard; trust the held-out validation split.
- Reproducibility: fixed seeds, pinned versions, everything runnable from `code/` in the zip.

## Pipeline commands (once built)
```bash
PYTHONUTF8=1 .venv/Scripts/python scripts/profile_dataset.py --data-dir dataset
# er CLI subcommands: split | analyze-gt | block | features | train | predict | evaluate | submit | validate
```
