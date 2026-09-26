# Amazon ML Challenge 2026: Large-Scale Multilingual Business Entity Resolution

[![Tests](https://img.shields.io/badge/Tests-120%20Passing-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)]()
[![Model](https://img.shields.io/badge/Model-LightGBM%20Gradient%20Boosting-orange.svg)]()
[![Metric](https://img.shields.io/badge/Validation%20Macro%20F0.5-0.851852-success.svg)]()
[![License](https://img.shields.io/badge/License-Proprietary-lightgrey.svg)]()

Production-grade, highly scalable entity resolution pipeline built for the **Amazon ML Challenge 2026**. The system matches noisy, multilingual business records from **Source 1** against candidates in **Source 2** and **Source 3**, spanning multiple geographic regions (including India, United States, and France), diverse scripts (Latin, Devanagari), and significant address/name noise.

---

## Architecture Overview

```
                                    +-----------------------+
                                    | Raw Input Datasets    |
                                    | (Source 1, 2, and 3)  |
                                    +-----------+-----------+
                                                |
                                                v
                                    +-----------------------+
                                    | Text Normalization    |
                                    | - Unicode NFKC & Case |
                                    | - Legal Suffix Strip  |
                                    | - Script-aware Tokens |
                                    +-----------+-----------+
                                                |
                                                v
                                    +-----------------------+
                                    | Candidate Generation  |
                                    | - Multi-pass Blocking |
                                    | - Rare Token IDF      |
                                    | - 50/50 Source Balance|
                                    | - Heavy Name Cap 100  |
                                    +-----------+-----------+
                                                |
                                    [ 34.57M Candidate Pairs ]
                                    [ 87.81% Pair Recall     ]
                                                |
                                                v
                                    +-----------------------+
                                    | 65-Feature Extraction |
                                    | - Exact & Reordered   |
                                    | - Token Jaccard/Dice  |
                                    | - Address & Numeric   |
                                    | - Fuzzy Levenshtein   |
                                    | - Missingness & Flags |
                                    | - Rank & Provenance   |
                                    +-----------+-----------+
                                                |
                                                v
                                    +-----------------------+
                                    | Controlled Sampling   |
                                    | - All Positives Kept  |
                                    | - Source-balanced Neg |
                                    | - Hard Mined Negatives|
                                    +-----------+-----------+
                                                |
                                                v
                                    +-----------------------+
                                    | LightGBM Matcher      |
                                    | - Optuna Tuned GBDT   |
                                    | - Monotone Constraints|
                                    | - F0.5 Thresholding   |
                                    +-----------+-----------+
                                                |
                                                v
                                    +-----------------------+
                                    | Final Assembly        |
                                    | - Multi-match Grouping|
                                    | - Strict Schema Valid |
                                    | - Output Submission   |
                                    +-----------------------+
```

---

## Key Achievements & Milestones

| Stage | Metric / Deliverable | Result |
| :--- | :--- | :--- |
| **Data Verification** | Distribution, noise, script, country analysis | 100% verified across train/test |
| **Normalization** | Unicode NFKC, German sharp-s, Devanagari, suffixes | Deterministic, idempotent |
| **Candidate Generation** | Candidate pairs generated | **34.57 Million pairs** |
| **Candidate Recall** | True match recovery rate | **87.81% pair recall** |
| **Candidate Ceiling** | Candidate-level $F_{0.5}$ ceiling | **0.948629** |
| **Candidate Policy** | Frozen Policy v1 | **Source-balanced 50/50 + heavy sorted-name cap 100** |
| **Feature Extraction** | Engineered matching features | **65 dense features** across 7 categories |
| **Feature Integrity** | Candidate identity & duplication audit | 0 missing, 0 duplicates, 0 NaN/Inf |
| **Negative Sampling** | 100% positive retention + hard mined negatives | Source-balanced hybrid sampling |
| **Pilot Modeling** | Deterministic Baseline vs Logistic vs LightGBM | **LightGBM reached 0.851852 Macro $F_{0.5}$** |
| **Automated Tests** | Unit, integration, invariant, and firewall suites | **120 Passing Tests** |

---

## Candidate Generation Policy (Frozen v1)

Candidate generation reduces billions of cross-product combinations $(S_1 \times (S_2 \cup S_3))$ to a tractable candidate set while maintaining high recall:

1. **Multi-Pass Blocking**:
   - Exact normalized name match.
   - Suffix-stripped name match.
   - Word-sorted token sequence match (order-invariant).
   - Inverted token-IDF indexing for high-signal discriminating words.
   - Geographic & country-constrained blocking.
2. **Deterministic Source-Balanced Pruning**:
   - Equal allocation: 50% Source 2 candidates, 50% Source 3 candidates.
   - Heavy name sorted block cap set at 100 candidates per query entity.
   - Provenance tracking via bitmask for multi-pass recovery attribution.

---

## 65-Dimensional Feature Engineering

The matcher extracts 65 dense, label-free features for every candidate pair:

* **Exact Matches (7 features)**: Normalized full name equality, no-suffix equality, sorted token equality, address equality, postal code match.
* **Token Similarities (16 features)**: Token Jaccard, Token Overlap Coefficient, Dice similarity, Cosine similarity on word embeddings, IDF-weighted token overlap.
* **Address & Numeric Consistency (14 features)**: Street number overlap, digit set Jaccard, postal code edit distance, conflicting number penalties, address containment.
* **Fuzzy String Metrics (12 features)**: Damerau-Levenshtein similarity, Jaro-Winkler ratio, prefix matching, longest common substring ratio.
* **Missingness & Schema Indicators (6 features)**: Missing address in S1, missing address in S2/S3, single-word entity flags.
* **Provenance & Candidate Rank (6 features)**: Rank within source, IDF score of triggering block, provenance bitmask values.
* **Script & Multilingual Context (4 features)**: Devanagari script indicator, Latin script indicator, cross-script translation match flags.

---

## Controlled Matcher Optimization

The model training and evaluation follows strict reproducibility and anti-leakage principles:

1. **Firewalled Validation**: Test and evaluation splits are cryptographically checksummed and guarded against leakage.
2. **Negative Mining**: Negatives are partitioned into easy, random, and hard mined negatives (high similarity non-matches).
3. **Model Selection**: LightGBM gradient boosted decision trees optimized under the competition's macro-averaged $F_{0.5}$ metric (prioritizing precision over recall).
4. **Optimal Threshold Tuning**: Post-prediction threshold sweep maximizing $F_{0.5}$ on held-out calibration entities.

---

## Project Structure

```
amazon-challenge/
├── code/
│   └── business_entity_resolution/
│       ├── pyproject.toml
│       ├── src/
│       │   └── er/
│       │       ├── candidates/       # Blocking, IDF capping, refinement & production policies
│       │       ├── features/         # 65 feature extractors (exact, token, fuzzy, address, etc.)
│       │       ├── matcher/          # LightGBM training, baselines, sampling, thresholding
│       │       ├── resources/        # Company suffix dictionaries & regex patterns
│       │       ├── evaluate.py       # Official competition FBeta scoring & diagnostics
│       │       ├── io.py             # Optimized Arrow/DuckDB/Parquet I/O handlers
│       │       ├── normalize.py      # Unicode NFKC, casefold, accent, and punctuation normalization
│       │       └── split.py          # Entity-level deterministic train/val/test split generator
│       └── tests/                    # 120 automated unit, integration, and firewall tests
├── scripts/                          # Experimentation, profiling, audit, and benchmark drivers
├── utils/
│   └── validate_submission.py        # Official format, schema, and syntax submission validator
├── work/                             # Frozen candidate policies, model files, and audit reports
├── PRD.md                            # Product Requirements Document
├── CHALLENGE.md                      # Amazon Challenge guidelines & problem formulation
├── IMPLEMENTATION_PLAN.md            # Phased architectural execution roadmap
├── ULTRA_PLAN.md                     # Deep engineering design specification
└── README.md                         # Project documentation
```

---

## Reproducibility & Testing

### Running the Test Suite
All 120 unit and pipeline tests can be executed via:
```bash
python -m unittest discover -s code/business_entity_resolution/tests
```

### Validating Final Submission Format
```bash
python utils/validate_submission.py --submission output/submission.tsv --test dataset/test/test_source1.tsv
```

---

## License & Usage
This repository contains proprietary engineering artifacts developed for the Amazon ML Challenge 2026.
