# Architecture Diagrams — Amazon ML Challenge 2026 Business Entity Resolution

> Companion to `amazon_ml_challenge_master_architecture.md`. All Mermaid diagrams collected in one
> place for rendering. Diagrams are authored with quoted labels and `<br/>` breaks for reliable
> `mmdc` (mermaid-cli) rendering. Status markers: ✅ Existing · 🟡 In progress · ⬜ Planned.
> Labels: [ORG] organizer · [PDD] project decision · [ENG] engineering · [EXP] experimental · [FUT] future.

## Index
1. D1 — Overall system architecture
2. D2 — Data ingestion + integrity
3. D3 — Candidate generation (four passes)
4. D3b — Candidate ranking + pruning (frozen policy v1)
5. D4 — Data split / leakage firewall
6. D5 — Feature engineering (pair → 65-d)
7. D6 — Negative sampling from candidates
8. D7 — Matcher (candidate → calibrated probability)
9. D8 — S1-level decision policy
10. D9 — Resource / partition strategy
11. D10 — Reproducibility / artifact chain
12. D11 — Training pipeline (build-time)
13. D12 — Test / inference pipeline
14. D13 — Submission / validation
15. D14 — Evaluation layers (L1–L4)

---

## D1 — Overall system architecture

```mermaid
flowchart TD
  DS["Challenge datasets<br/>S1 / S2 / S3 (+GT train only) [ORG]"] --> IV["Data ingestion + validation<br/>typed columnar store, integrity checks"]
  IV --> NR["Normalization + representations<br/>frozen normalize.py ✅"]
  NR --> CG["Frozen candidate generation v1<br/>4 label-free passes ✅"]
  CG --> CA["Candidate artifacts<br/>16 hash partitions, checksummed ✅"]
  CA --> FE["Pair feature extraction<br/>65-d pilot done, full-scale 🟡"]
  FE --> CM["Candidate matcher<br/>tabular GBM (smoke fit) 🟡"]
  CM --> DP["S1-level decision policy<br/>threshold + singleton + multi-match ⬜"]
  DP --> MR[["matching_results.tsv"]]
  CA --> CP[["candidate_pairs.tsv (exact set)"]]
  MR --> VAL["Official validator<br/>validate_submission.py ✅"]
  CP --> VAL
```

## D2 — Data ingestion + integrity

```mermaid
flowchart LR
  RAW["Raw TSV<br/>train + test (S1/S2/S3)"] --> ING["Ingest once<br/>typed columnar Parquet"]
  ING --> IDC["Integer-code IDs<br/>keep string id for emit"]
  IDC --> INT["Integrity checks<br/>uniqueness, disjoint ns, GT resolves"]
  INT -->|"0 missing / 0 dup / ≤1 S1 per target"| OK["Verified store<br/>DuckDB + Parquet"]
  INT -->|violation| STOP["Halt: report, do not proceed"]
```

## D3 — Candidate generation (four passes → union → rank)

```mermaid
flowchart TD
  N["Normalized S1 + S2/S3 keys<br/>frozen normalize.py ✅"] --> P1["P1 sorted_name<br/>bit 1 · recall 0.5061"]
  N --> P2["P2 exact_address<br/>bit 2 · recall 0.0832"]
  N --> P3["P3 name_token<br/>bit 4 · recall 0.5317"]
  N --> P4["P4 address_token<br/>bit 8 · recall 0.7965"]
  P1 --> U["Union + dedup<br/>provenance = bitwise OR"]
  P2 --> U
  P3 --> U
  P4 --> U
  U --> R["IDF evidence rank<br/>score = Σ 1/target_df(token)"]
  R --> PR["Per-S1 pruning + quotas<br/>frozen policy v1 (D3b)"]
```

## D3b — Candidate ranking + pruning (frozen policy v1)

```mermaid
flowchart TD
  RANKED["Ranked union<br/>evidence_score per pair"] --> SRC["Source 50/50 quota<br/>balance S2 vs S3 per S1"]
  SRC --> HVY["Heavy sorted-name cap 100<br/>for S1 blocking ≥120 records"]
  HVY --> ART["Frozen artifact<br/>34,568,979 rows · 16 shards ✅"]
  ART --> M1["recall 0.878084<br/>p99 213 · max 292"]
  ART --> M2["checksum 221fef24…9465b067<br/>identity 0/0/0"]
```

## D4 — Data split / leakage firewall

```mermaid
flowchart TD
  GT["Train S1 + GT<br/>2,206,821 S1"] --> H["MD5(entity_id ‖ seed)<br/>deterministic bucket"]
  H --> MT["model_train<br/>1,765,608"]
  H --> MC["model_calibration<br/>110,341 (τ tuning)"]
  H --> ME["model_final_eval<br/>110,341 (locked)"]
  H --> CD["candidate_dev<br/>220,531 (policy)"]
  MT -.->|"S1 overlap 0<br/>target overlap 0"| ME
```

## D5 — Feature engineering (pair → 65-d vector)

```mermaid
flowchart LR
  PAIR["Candidate pair<br/>(S1 record, target record)"] --> G1["provenance"]
  PAIR --> G2["exact"]
  PAIR --> G3["missing_length"]
  PAIR --> G4["token"]
  PAIR --> G5["numeric"]
  PAIR --> G6["fuzzy (rapidfuzz)"]
  PAIR --> G7["script / translit"]
  G1 --> V["65-d float32 vector<br/>+ train-only label"]
  G2 --> V
  G3 --> V
  G4 --> V
  G5 --> V
  G6 --> V
  G7 --> V
```

## D6 — Negative sampling from candidates

```mermaid
flowchart TD
  CAND["Frozen candidates (dev)<br/>+ GT labels"] --> POS["Positives<br/>GT ∩ candidates = 670,785"]
  CAND --> HN["Hard negatives<br/>high-sim non-matches (keep)"]
  CAND --> EN["Easy negatives<br/>low-sim non-matches (subsample)"]
  MISS["Candidate misses<br/>93,134 unreached"] -->|EXCLUDED| X["not positives, not negatives"]
  POS --> TR["Matcher training set<br/>(model_train split)"]
  HN --> TR
  EN --> TR
```

## D7 — Matcher (candidate → calibrated probability)

```mermaid
flowchart LR
  FM["65-d feature matrix<br/>(per hash-partition)"] --> M["Tabular GBM<br/>LightGBM / XGBoost ⬜"]
  M --> RAW["raw score"]
  RAW --> CAL["Calibration<br/>isotonic/Platt on model_calibration"]
  CAL --> P["Calibrated P(match) per pair<br/>NOT top-1"]
  P --> DEC["→ S1 decision policy (D8)"]
```

## D8 — S1-level decision policy

```mermaid
flowchart TD
  P["Calibrated P(match)<br/>for each candidate of S1 e"] --> T{"P ≥ τ ?<br/>(τ tuned on calibration)"}
  T -->|"none clear τ"| S["Emit empty set<br/>singleton = 1.0 if true (O4)"]
  T -->|"≥1 clears τ"| K["Keep all that clear τ<br/>0..many, S2+S3 (O2/O3)"]
  K --> INV["Invariant: M(e) ⊆ C(e) (O13)"]
  S --> OUT[["matching_results.tsv row"]]
  INV --> OUT
```

## D9 — Resource / partition strategy

```mermaid
flowchart TD
  BIG["S1×(S2∪S3) evidence<br/>hundreds of millions pre-prune"] --> DUCK["DuckDB bounded memory_limit<br/>+ temp_directory spill"]
  DUCK --> HP["16 hash partitions<br/>hash(source1_entity_id)%16"]
  HP --> S0["shard 0"]
  HP --> SE["…"]
  HP --> S15["shard 15"]
  S0 --> PROC["per-shard: features → score<br/>load, compute, write, release"]
  S15 --> PROC
  PROC --> ATOM[".partial atomic write<br/>+ per-part SHA-256 (resume-safe)"]
```

## D10 — Reproducibility / artifact chain

```mermaid
flowchart LR
  SEED["Fixed seeds + pinned reqs<br/>Python 3.12, PYTHONUTF8=1"] --> INP["Frozen inputs (SHA-pinned)<br/>normalize / evaluate / split / ranker"]
  INP --> GEN["Candidate generation (frozen policy)"]
  GEN --> ART["16-part artifact<br/>34,568,979 rows"]
  ART --> MAN["Manifests + per-part SHA-256"]
  MAN --> GATE{"Freeze gate<br/>0 added / 0 removed / 0 dup?"}
  GATE -->|"equal=true"| PASS["Reproducible ✅ (checksum 221fef24…)"]
  GATE -->|diff| FAIL["Not reproducible: investigate"]
```

## D11 — Training pipeline (build-time)

```mermaid
flowchart TD
  A["Frozen candidates + labels<br/>(model_train split)"] --> B["Phase 8 features 65-d 🟡"]
  B --> C["Phase 9 negatives from candidates ⬜"]
  C --> D["Phase 10 GBM bake-off ⬜"]
  D --> E["Phase 11 calibrate + tune τ<br/>on model_calibration ⬜"]
  E --> F["Phase 12 decision policy ⬜"]
  F --> G["Lock model + τ → single L4 read<br/>on model_final_eval"]
```

## D12 — Test / inference pipeline

```mermaid
flowchart TD
  T["Test S1/S2/S3<br/>1,732,544 S1 (France 259,452)"] --> NZ["Normalize (frozen) ✅"]
  NZ --> CGT["Candidate gen — frozen policy v1 ✅<br/>16 shards"]
  CGT --> CPQ[["candidate_pairs.tsv (exact set) O11/O13"]]
  CGT --> FET["Features 65-d per shard 🟡"]
  FET --> SC["GBM → calibrated P(match) ⬜"]
  SC --> DEC["Global τ + singleton path ⬜"]
  DEC --> MRT[["matching_results.tsv"]]
  MRT --> V["validate_submission.py → PASS ✅"]
  CPQ --> V
```

## D13 — Submission / validation

```mermaid
flowchart TD
  DEC["Decision output M(e)"] --> WM["Write matching_results.tsv<br/>exact format, no strip"]
  FROZ["Frozen candidate artifact"] --> WC["Write candidate_pairs.tsv<br/>exact set"]
  WM --> SUB{"validate_submission.py"}
  WC --> SUB
  SUB -->|PASS| REL["Release-eligible<br/>(+ record L4 macro-F₀.₅)"]
  SUB -->|FAIL| FIX["Reject: fix format, re-run"]
```

## D14 — Evaluation layers (L1–L4, never conflated)

```mermaid
flowchart LR
  L1["L1 Candidate pair recall<br/>0.878084 (diagnostic, O19)"] --> L2["L2 Candidate oracle macro-F₀.₅<br/>0.948629 (score ceiling)"]
  L2 --> L3["L3 Matcher pair P/R<br/>pilot 0.9365/0.7345 🟡"]
  L3 --> L4["L4 End-to-end macro-F₀.₅<br/>model_final_eval ⬜"]
```

---

*14 diagrams. D1–D14 cover: overall system, data ingestion, candidate generation, candidate
ranking/pruning, split/leakage, feature engineering, negative sampling, matcher, S1 decision,
resource/partition, reproducibility/artifact, training pipeline, test/inference, submission/validation,
and evaluation layers — the full mandated set plus data-ingestion, negative-sampling, and
evaluation-layer diagrams. Source of truth for all figures: the frozen manifests and reports cited in
`amazon_ml_challenge_master_architecture.md`.*
