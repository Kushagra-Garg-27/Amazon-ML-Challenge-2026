# PRD — Business Entity Resolution Challenge

Product requirements for a reproducible, offline, competitive ER pipeline. Companion docs:
`CLAUDE.md` (operating rules), `ULTRA_PLAN.md` (phase execution), `IMPLEMENTATION_PLAN.md`
(task backlog). All numbers here are **confirmed by profiling**; results/benchmarks are
marked *TBD (measured)* and must never be invented.

## 1. Document control
Owner: ER pipeline (lead ML eng agent). Status: living. Source of truth for scope & metrics.
Change policy: update alongside `ULTRA_PLAN.md`; every empirical claim links to a ledger run id.

## 2. Problem & business context
Business identity data arrives from 3 independent, noisy sources with no shared keys. S1 is a
**deduplicated reference**; S2/S3 are noisy, internally-duplicated. Goal: for each S1 entity,
recover all S2/S3 records referring to the same real-world business. Mirrors Amazon-scale ER
where false merges are costly.

## 3. Goals / non-goals
Goals: (a) high macro-F_0.5; (b) high blocking recall at **small candidate-set size** (separately
ranked); (c) reproducible, memory-frugal, documented pipeline. Non-goals: online serving,
UI, cross-entity clustering of S2↔S3, any external data lookup, LLM-based matching by default.

## 4. Success metrics
- **Primary:** macro-F_0.5 on held-out validation (proxy for private leaderboard).
- **Blocking:** candidate recall (fraction of true pairs retained), reduction ratio
  (1 − pairs_kept/all_possible), candidate-set size per S1 (mean/median/p90/p95/p99/max),
  zero-candidate S1 count, true pairs lost.
- **Engineering:** end-to-end wall-clock, peak RAM, deterministic re-runs.
Targets are set as acceptance criteria in `ULTRA_PLAN.md`; actuals come from the ledger.

## 5. Evaluation methodology
Implement `F_0.5 = 1.25PR/(0.25P+R)` per S1, macro-averaged incl. singletons (empty-correct=1.0,
false-merge-on-singleton=0.0). Hold out a **stratified validation split** from train
(stratify by country × match-count bucket) so the split mirrors test composition as far as
possible. France cannot be stratified (absent in train) — approximate its zero-shot behavior
by a **leave-one-country-out** probe (train on US, score India; and vice-versa) to estimate
generalization to an unseen country. Never tune on the public leaderboard.

## 6. Data sources & schema
Files `{split}_source{1,2,3}.tsv`, tab-separated. Columns: `entity_id` (prefix S1-/S2-/S3-),
`business_name`, `business_address`, `country`. GT: `source1_entity_id`, `matched_entity_ids`
(comma list; empty = singleton). Source identity = id prefix + file. Read with explicit `\t`.

## 7. Data profile (confirmed)
Sizes in AUDIT/§CLAUDE. Name p50≈24 chars / 3.5 tokens; address p50≈41 chars. No empty names;
~3% empty S2/S3 addresses. Country clean. Train {US,India}; test {US,India,France}.

## 8. Ground-truth structure & modeling implications
Avg 3.46 matches/S1; only 5.6% singletons, 5.4% exactly-one, **89% ≥2 matches**, max 11.
80.5% match both S2 and S3; 76.8% match ≥2 within one source (S2/S3 are internally
duplicated). Implications: (1) predict **sets**, not top-1; (2) decision is **per-candidate
thresholding**, not argmax; (3) the empty-prediction prior is weak (only 5.6%) — being
over-conservative forfeits recall on 94% of entities, but F_0.5 still punishes junk, so
calibrate the threshold to macro-F_0.5; (4) matches likely stay **within country** — verify,
then partition blocking by country (huge search-space cut).

## 9. Noise taxonomy (drives normalization & features)
Name: legal-suffix variants (Corp/Corporation, Pvt/Private, Ltd/Limited, LLP, Inc, Sarl, SAS,
SCI), `&`↔`and`, punctuation, word-order transposition, typos, abbreviations, DBA/trade names,
leading junk (`--`,`<<`), domains-as-names, Devanagari vs romanized. Address: St/Street,
Rd/Road, R./Rue abbreviations, transliteration, missing PIN/state, landmark refs (“Near SBI
ATM”), municipal numbering, component reordering, ~3% empty.

## 10. Open-set country / France zero-shot
France = 15% of test S1, **no training rows**. Requirement: pipeline must not hard-code,
filter, or one-hot to {US,India}. Country used only as an **agreement/equality** signal and to
partition blocking; normalization must be locale-general (Unicode accent folding, generic
suffix/street dictionaries extended to FR tokens). Model features must be country-agnostic
similarities so a US/India-trained model transfers to FR.

## 11. Fair-play constraints
No external business-identity lookup/enrichment of any kind (see CLAUDE.md). Only provided
data + deterministic local normalization. Any violation = disqualification. Web only for
generic technical docs.

## 12. Assumptions (to validate, not assume-and-forget)
A1 matches are within the same country (verify in Phase 3). A2 each S2/S3 record matches ≤1 S1
(S1 deduped). A3 name is the strongest signal; address disambiguates. A4 Devanagari↔Latin
cross-script pairs exist in India GT (measure rate). Each assumption has a Phase-3 check.

## 13. Risks & mitigations
R1 **Memory (0.8GB)** → DuckDB/polars streaming, sparse, int IDs, batching. R2 **Cross-script
India** → transliteration-fold + script-robust char features; measure impact. R3 **France
zero-shot** → country-agnostic features + LOCO probe. R4 **Blocking misses cap recall** →
multi-pass union, measure recall ceiling before modeling. R5 **False merges** (F_0.5) →
precision-first threshold, hard-negative mining. R6 **Overfitting LB** → held-out validation.
R7 **Runtime blowup** → per-stage runtime budget + checkpoints.

## 14. Constraints
Memory ~0.8GB; offline; final model MIT/Apache ≤8B (GBM chosen); Windows + Python 3.12 venv;
deterministic (seeded); everything reproducible from `code/`.

## 15. Architecture (stages)
`normalize → block (multi-pass, per country) → prune candidates → pairwise features →
GBM scorer → per-candidate threshold (F_0.5-tuned) → singleton/multi-match assembly →
write matching_results + candidate_pairs → validate → package`.

## 16. Normalization spec
Deterministic, stdlib-first. Steps: Unicode NFKC → casefold → strip leading junk/punctuation →
map `&`→`and` → collapse whitespace → accent-fold (NFKD + drop combining marks) for Latin →
legal-suffix canonicalization via a curated dict (multi-locale) → optional Devanagari→Latin
transliteration for a parallel key. Emit multiple keys: `name_norm`, `name_nosuffix`,
`name_sorted_tokens`, `name_acronym`, `addr_norm`, plus numeric tokens (PIN/street numbers).
Keep raw too. No external data. Unit-test each rule on known variants.

## 17. Blocking / candidate generation spec
Per-country. Multi-pass union of cheap, high-recall keys, then a bounded prune:
(P1) exact `name_nosuffix` key; (P2) sorted-token key; (P3) rare-token inverted index
(share a low-DF token); (P4) char n-gram (3–4) TF-IDF top-k nearest by cosine; (P5)
address numeric/PIN + name-token co-block. Union candidates, cap per S1 at K (tuned), rank by
a cheap similarity for the cap. Implemented out-of-core (DuckDB joins / polars). Output the
**final** candidate set fed to the model = `candidate_pairs.tsv`.

## 18. Blocking evaluation spec
On validation GT: candidate recall (kept true pairs / all true pairs), reduction ratio,
per-S1 size distribution (mean/median/p90/p95/p99/max), zero-candidate S1 count, list of true
pairs lost (error-analyze). Objective: maximize recall while **minimizing mean candidate-set
size** (separately ranked). Report the recall–size Pareto curve; pick the knee.

## 19. Feature engineering spec
Per (S1, candidate) pair. Name: token Jaccard, token-sort/set ratio, Levenshtein/edit ratio,
char 3-gram Jaccard, TF-IDF cosine, acronym match, suffix-stripped equality, length ratio,
prefix overlap. Address: token Jaccard, numeric/PIN overlap, city/state token overlap,
containment, empty-flags. Cross-field: name×addr combos, country agreement, missing-field
indicators, source (S2/S3) flag. Blocking-provenance: which passes retrieved it, retrieval
rank, #passes. All computed with rapidfuzz/sparse; batched. Country-agnostic by construction.

## 20. Matching model spec
Tabular binary classifier over pair features; label = in GT. **Compare empirically**:
LightGBM, XGBoost, logistic (baseline), maybe hist-GBM; pick by validation macro-F_0.5 +
runtime. Class imbalance handled via blocking (negatives = non-matched candidates) + hard-
negative mining. No LLM. Calibrate probabilities (isotonic/Platt) for thresholding.

## 21. Decision policy
Per-candidate: predict match if `p ≥ τ`. Tune single global τ to maximize macro-F_0.5 on
validation; then test **per-country** and **per-match-count** τ variants only if they beat
global with evidence. Optionally a light top-k cap informed by the match-count prior (max 11).

## 22. Singleton handling
An S1 with no candidate above τ → empty prediction (scores 1.0 if truly singleton). Because
F_0.5 punishes false merges hard, the precision-heavy τ naturally protects singletons; measure
singleton precision/recall separately. Consider a dedicated “has-any-match” gate if it helps.

## 23. Multi-match handling
Dominant case (89% ≥2). Independent per-candidate thresholding already yields sets. Ensure
feature/threshold design doesn't cap at 1. Watch both-source (80.5%) and within-source-multi
(76.8%) recovery in error analysis.

## 24. Cross-script (India) strategy
Measure Devanagari↔Latin match rate in GT first. If material: add a transliteration-normalized
key + char-level features that survive script; consider matching on numeric/address anchors
when names are cross-script. Keep everything deterministic/local.

## 25. Validation & scoring harness
`er evaluate` computes macro-F_0.5 + P/R + singleton stats + per-country breakdown from a
predictions file vs a GT file. Reused for every experiment. Deterministic.

## 26. Experiment tracking
Append-only ledger (JSONL or DuckDB table): run id, timestamp, stage, config/params, git-less
content hash of code, metrics (F_0.5/P/R/blocking-recall/reduction/sizes/runtime/peak-RAM),
notes. Every kept decision cites a run id. No claim without a run.

## 27. Reproducibility & environment
Python 3.12 venv; pinned `requirements.txt` (see §CLAUDE versions); fixed seeds; UTF-8 I/O;
out-of-core. `README.md` documents end-to-end re-run: data → blocking → matching → outputs.

## 28. Output/submission spec
Two TSVs in `output/`. Exact headers, TABs, one row per test S1, singleton=empty, S2/S3-only
IDs, no dup IDs/rows, no stray spaces (validator doesn't strip). Matches ⊆ candidates. Gate on
`utils/validate_submission.py` → PASS.

## 29. Packaging spec
`<team>_submission.zip` → `output/{matching_results.tsv,candidate_pairs.tsv}`,
`code/business_entity_resolution/{src/,README.md,requirements.txt}`,
`Documentation_template.md` (filled). Must regenerate outputs from `code/` alone.

## 30. Tooling decisions (MCP / DB / skills)
**MCP:** add none for data — external business MCPs violate fair play; keep context-mode for
context only. **DB:** DuckDB (embedded, out-of-core, MIT) for heavy blocking joins + the
experiment ledger; no hosted DB. **Skills/CLI:** one reproducible `er` CLI with subcommands
(`split, analyze-gt, block, features, train, predict, evaluate, submit, validate`) under
`src/`; these are the reusable “skills,” guaranteed to run in the graded package.

## 31. Milestones
See `ULTRA_PLAN.md` Phases 0–18 and `IMPLEMENTATION_PLAN.md` task backlog (ER-###).

## 32. Open questions (resolve empirically, Phase 3)
Q1 Are matches always within-country? Q2 Cross-script India match rate? Q3 How separable are
true vs blocking-false candidate scores? Q4 Best blocking recall achievable at mean size ≤K?
Q5 Does per-country τ beat global? Q6 Does the model trained on US/India transfer to FR (LOCO)?
