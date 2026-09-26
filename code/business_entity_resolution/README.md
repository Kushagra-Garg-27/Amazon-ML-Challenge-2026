# Business Entity Resolution — Reproducible Pipeline

Self-contained ER pipeline for the challenge. See repo-root `PRD.md`, `ULTRA_PLAN.md`,
`IMPLEMENTATION_PLAN.md`, and `CLAUDE.md` for design, phases, task backlog, and hard rules.

## Environment
- Python **3.12** (system 3.14 lacks ML wheels — do not use it).
- `python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt`
- All deps permissively licensed (BSD/MIT/Apache). Final model: LightGBM/XGBoost (MIT/Apache, ≤8B).

## Constraints (must hold)
- **~0.8 GB free RAM** → out-of-core (DuckDB spills to disk, polars streaming, sparse, int IDs, batched).
- **Offline only** — no external business-identity lookup/enrichment (fair play; disqualifying).
- UTF-8 everywhere; run with `PYTHONUTF8=1`.

## End-to-end reproduction (once built)
```bash
PYTHONUTF8=1 .venv/Scripts/python -m er ingest      --data-dir dataset
PYTHONUTF8=1 .venv/Scripts/python -m er split        # stratified holdout for scoring
PYTHONUTF8=1 .venv/Scripts/python -m er analyze-gt    # validate assumptions (within-country, cross-script)
PYTHONUTF8=1 .venv/Scripts/python -m er block         # multi-pass candidate generation
PYTHONUTF8=1 .venv/Scripts/python -m er candidates    # union + prune + cap  -> candidate_pairs
PYTHONUTF8=1 .venv/Scripts/python -m er features
PYTHONUTF8=1 .venv/Scripts/python -m er train         # compare logistic / LightGBM / XGBoost
PYTHONUTF8=1 .venv/Scripts/python -m er threshold     # F_0.5-optimized decision threshold
PYTHONUTF8=1 .venv/Scripts/python -m er predict
PYTHONUTF8=1 .venv/Scripts/python -m er submit        # writes output/*.tsv, runs the validator
```
Outputs: `output/matching_results.tsv`, `output/candidate_pairs.tsv` (matches ⊆ candidates).

## Metric
Macro-averaged `F_0.5 = 1.25·P·R/(0.25·P+R)` per Source-1 entity, singletons included.
