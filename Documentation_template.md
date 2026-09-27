# ML Challenge 2026: Business Entity Resolution Solution

**Team Name:** broCode  
**Team Members:** Kushagra Garg and Tanisha Mandavia  
**Submission Date:** 2026-09-27

## 1. Executive Summary

We resolve each Source 1 business against Sources 2 and 3 with open-set country blocking followed by a frozen LightGBM classifier. The blocker combines exact sorted-name and normalized-address joins with inverse-document-frequency name and address token retrieval. A source-balanced quota and an evidence-ranked cap on heavy exact-name blocks control candidate size. The final model uses 61 ordered numeric features and one global threshold of 0.61.

## 2. Data and integrity

The test source files contain 1,732,544 S1, 4,887,273 S2, and 5,082,316 S3 records. Source 1 country counts are India 809,986, US 663,106, and France 259,452. Raw address fields are blank in 0 S1, 129,408 S2 and 136,098 S3 records. Every raw test source has the valid four-column TSV schema, unique source-prefixed IDs, and disjoint S2/S3 prefixes. Raw IDs and fields are preserved exactly in normalized keys. The training set contains the only supplied labels; test labels are unavailable and were not used.

## 3. Normalization and candidate generation

The fixed normalization implementation uses Unicode NFKC, casefolding, accent and punctuation handling, company suffix normalization, token sorting, and address number extraction. Country is an open-set string used for equality within candidate retrieval. France receives the same code path and frozen policy as India and US.

The four retrieval passes are exact sorted name, exact normalized address, eligible name token, and eligible address token. Token eligibility uses target document frequency at most 2,000 within country. Name and address shortlists are ranked by overlap across passes, postal and numeric agreement, exact name and address evidence, inverse-frequency score, overlap size and coverage, token Jaccard, then target ID. Each source gets at most 50 token candidates per pass. Exact address is uncapped. A sorted-name block with at least 120 targets retains the top 100 sorted-name-only candidates; candidates reached by another pass survive that cap. Provenance bits are OR-ed by `(S1,target)` before pruning. The final post-pruning pair set is scored by the model and written to `candidate_pairs.tsv`.

On the one-time held-out final evaluation, this policy generated 17,295,784 unique candidates for 110,341 S1 entities, a mean of 156.75 and median of 180 per S1. The candidate oracle macro F0.5 ceiling was 0.949598 and candidate pair recall was 0.879015. These are train-label evaluation results; no test recall or France accuracy is claimed.

On the full label-free test set, the same policy generated **273,502,145** unique post-pruning pairs across 96 checksummed physical parts: 135,832,608 S2 and 137,669,537 S3. All 1,732,544 S1 records were covered; 1,346 had zero candidates. The maximum observed per S1 was 296. These counts describe retrieval structure, not accuracy.

By country, the candidate totals were France 40,719,179 (6 zero-candidate S1), India 124,118,027 (1,040), and US 108,664,939 (300). France's candidate volume was recorded as a structural result; it did not trigger a policy change.

## 4. Features and matcher

Physical feature v1 materializes 65 columns: retrieval provenance and ranks, source identity, exact normalized fields, name and address lengths, token overlap and containment, inverse-frequency overlap, numeric and postal agreement/conflict, fuzzy ratios, and script compatibility. Feature specification v1.1 selects 61 fixed ordered float32 model inputs, removing four redundant columns while leaving physical feature values unchanged. Model checksum and feature order are checked before inference.

All recovered positive training pairs were retained. The selected negative-sampling policy used one round of mined false positives (up to ten per source/S1) plus two random negatives per source/S1. A bounded model-fit population trained the native LightGBM binary classifier for 180 rounds with learning rate 0.05, 31 leaves, depth 8, minimum leaf size 100, 0.9 feature and bagging fractions, L2 penalty 1, two threads, and seed 42. LightGBM version is 4.7.0. The model is an MIT-licensed tree ensemble, within the challenge size limit.

One coarse and one fine threshold grid on the protected threshold split selected 0.61 for macro F0.5. Inference accepts every candidate scoring at least 0.61, allows empty and multiple match sets, and applies no top-1, source-specific threshold, or numeric-conflict post-filter.

## 5. Validation and error analysis

An entity-level split separated model fit, tuning, threshold selection, and one-time final evaluation. Frozen model, candidate policy, feature specification and threshold checksums were verified before final labels were opened. The held-out release gate passed: macro F0.5 **0.896256**, pair precision **0.970847**, pair recall **0.803379**, singleton accuracy **0.893872**. The macro 95% S1 bootstrap interval was [0.895048, 0.897387]. India and US macro F0.5 were 0.877142 and 0.909125; S2 and S3 pair recall were 0.807622 and 0.799397.

Address availability is a major weakness. Pair recall was 0.828173 when both addresses were present and 0.269730 when either was missing. The held-out analysis counted 46,199 candidate misses, 28,882 matcher false negatives, and 9,212 matcher false positives. France is a zero-shot test country; only structural, label-free diagnostics are appropriate. No test accuracy or leaderboard estimate is asserted.

## 6. Resources and reproducibility

The measured held-out run used 1,086.96 seconds for candidates, 1,253.25 seconds for physical features, and 86.28 seconds for scoring. Measured peak process RSS was approximately 1.04 GB, 1.20 GB, and 1.49 GB respectively. Full test candidate generation took 3.745 hours of part runtime, with sampled peak process RSS 0.974 GB, sampled peak DuckDB temporary disk 1.397 GB, and minimum sampled free RAM 0.528 GB. One planned restart lowered DuckDB's internal limit from 700 to 500 MB after RAM approached the 0.5 GB stop guard; all completed receipts were reused and the frozen candidate semantics were unchanged. The test plan reserves at least 48 GB free scratch disk and 1.35 GB free RAM before each stage, uses 96 restartable country/hash work units, one large data process, atomic Parquet writes, and SHA-256 receipts. The original 9.6-hour projection excluded key ingestion, rank construction and retries; actual test run measurements are recorded in release manifests.

`code/business_entity_resolution/README.md` provides the environment, stage, resume and validator commands. `code/business_entity_resolution/requirements.txt` pins release dependencies. Reproduction requires only the supplied train/test TSVs and packaged code/model/configuration. The two output TSVs contain every raw test S1 exactly once; empty lists retain the explicit second TSV field. Independent audit and the challenge validator are release gates.

## 7. Limitations and fair play

The blocker cannot recover true matches with no exact or eligible token evidence, and missing addresses reduce recall. France is processed without country-specific tuning; its score and candidate distributions may differ from India and US, but these differences alone do not change the frozen policy. The methodology uses only challenge data and permissively licensed runtime libraries. No external business identity source, lookup API, registration database, geocoder, or enrichment service was used. No test labels, model retraining, or post-final-evaluation policy change was used.

## Appendix: frozen identities

- Candidate policy SHA-256: `46fd324b3d4db7dbdf5fa4b8ce528f681335ce16f759fb93a39ba5b98a040fea`
- Feature v1.1 SHA-256: `37982fd6377aa2e36fe1c5dec0484a35c02ba4bcc4e3b67b3a337b33597f6e1f`
- Model SHA-256: `76ff78a7cc97e40174b6d631a6eaa062c786f0480c6693fd6387e90be6761d21`
- Inference policy SHA-256: `ced4ee4133110f5f55c184c3d4020b5087ecac5398bdf9ebfd03f76b3662aba3`
