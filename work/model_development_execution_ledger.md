# Controlled matcher execution ledger

- Ruling: used approximately 94/3/3 inside model_train instead of suggested 90/5/5 — kept both protected subsets above 50,000 S1 while fitting the measured 3.91 GiB free-memory envelope — cost if wrong: smaller tune/threshold populations may increase metric variance.
- Ruling: forced all prior 5,000 pilot training S1s into model_fit — prevents prior label exposure from entering protected subsets — cost if wrong: negligible fit-assignment skew.
- Ruling: physically split large US tune and threshold populations by a deterministic hash — preserves logical membership while avoiding DuckDB final-sort allocation failures — cost if wrong: partition union checks would expose missing/duplicate candidates.
- Ruling: feature v1.1 preserves 65-column physical artifacts but removes four redundant model inputs — avoids silent mutation and retains restart compatibility — cost if wrong: a removed redundant view could help tree optimization despite carrying no new information.
- Ruling: all non-fuzzy and except RapidFuzz are one identical ablation — one materialized experiment is reported under both requested names — cost if wrong: none, the column identities are equal.
- Ruling: skipped Tier D — Tier C improved macro F0.5 by 0.000879 over Tier B while more than doubling sampled rows — cost if wrong: a larger tier could deliver an unmeasured gain above Tier C.
- Ruling: source-specific thresholds were not compared on model_threshold — tune-only S2 and S3 optima were both 0.70, failing the predeclared 0.10 gap gate — cost if wrong: source calibration could differ in subtler ways.
- Final self-review: fixed estimated mined hard/random counts by exact rank reconstruction; totals now sum exactly to all negatives.
- Final self-review: no Critical, Important, or deferred Minor findings remain after the fix and full-suite verification.
