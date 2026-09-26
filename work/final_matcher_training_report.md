# Final matcher development training report

> Development freeze only. `model_final_eval` has not been evaluated.

## Current-state and implementation audit

Frozen candidate policy, feature-v1, and top-level split checksums matched. Initial tests: 102 passed in 27.291s, exit 0. Free RAM was 3.911 GiB and free disk was 216.687 GiB; no Python/DuckDB workload was active.

The NumPy logistic baseline optimizes class-weighted binary cross-entropy with L2 on coefficients, z-score scaling, clipped logits, seeded shuffled mini-batches, and fixed 18 epochs. It has no convergence-based stop. Coefficients, intercept, means, scales, feature order, and seed are plain JSON.

The LightGBM smoke used 65 float32 numeric inputs, no categorical declaration or class weights, native missing handling, fixed seed 42, two threads, separate pilot train/calibration populations, and a whole-process peak covering load, Dataset construction, training, and prediction.

## Development firewall

# Model-development split firewall

Seed: `model_development_v1_20260926`.

The frozen top-level split is unchanged. Every previously used model-train pilot S1 is forced into `model_fit`. The remaining rows use the first byte of `md5(entity_id || seed)`: `00`-`07` for `model_tune`, `08`-`0f` for `model_threshold`, and all other bytes for `model_fit`.

The approximately 94/3/3 allocation is a resource-safe adjustment from the suggested 90/5/5 split. Each protected subset still contains more than 50,000 S1s.

| Split | S1 | ID checksum |
|---|---:|---|
| model_fit | 1,655,792 | `0677a4f86631f315a9a9e44d6d94e7e4d24f382f9f522d4db283b6b4fca9c659` |
| model_tune | 54,725 | `cda2f771e68a4647d37caa4bee6f20340bd1e35af5953fc0b0b296fa98654fae` |
| model_threshold | 55,091 | `318b2c77c328e11dd69f4f8b87210e434f1fd6aaded5c6354a845845c4c35a2e` |

S1 overlap: **0**. Target-ID overlap: **0**. Prior pilot rows outside model_fit: **0**.

`model_calibration` is reclassified as `baseline_dev`. It is absent from this file because the frozen top-level manifest remains authoritative.

At split creation, `model_threshold` has membership, country, count, and an ID checksum only. No threshold labels, candidates, features, predictions, or metrics were read or materialized.

## Feature audit

# Feature v1 audit

Audited 65 features on a deterministic 100,000-row correlation sample and the full bounded pilot population.

Original spec: `51f1a33eab22780fee39e29f69117faf7c8c6eb47658499c6f53f564c5245582`. Corrected v1.1 spec: `37982fd6377aa2e36fe1c5dec0484a35c02ba4bcc4e3b67b3a337b33597f6e1f`.

v1.1 corrects `conflicting_address_numbers` prose and omits four redundant model inputs. The physical v1 artifacts, numeric values, and 65 stored columns remain unchanged; the v1.1 training matrix has 61 ordered inputs.

Exact duplicate pairs on the sample: `[['retrieved_sorted_name', 'exact_name_sorted'], ['retrieved_exact_address', 'exact_address_norm'], ['exact_postal_token', 'shared_postal_tokens']]`.
Perfect-correlation pairs on the sample: `[['target_is_s2', 'target_is_s3', -1.0]]`.
Constant features: `['country_agreement', 's1_name_missing', 'target_name_missing', 's1_address_missing', 's1_script_class']`. Near-constant features: `['country_agreement', 's1_name_missing', 'target_name_missing', 's1_address_missing', 's1_script_class']`.

No label leakage or test-time unavailable input was found. Country is represented only by open-set equality.

| Feature | Type | Min | Median | Max | Std | Unique~ | Positive mean | Negative mean |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| provenance | uint8 | 1 | 8 | 15 | 2.61209 | 17 | 8.84492 | 5.84676 |
| retrieved_sorted_name | bool | 0 | 0 | 1 | 0.343172 | 2 | 0.576622 | 0.12764 |
| retrieved_exact_address | bool | 0 | 0 | 1 | 0.047065 | 2 | 0.0928043 | 0.000425561 |
| retrieved_name_token | bool | 0 | 0 | 1 | 0.475598 | 2 | 0.514168 | 0.34237 |
| retrieved_address_token | bool | 0 | 1 | 1 | 0.497722 | 2 | 0.753252 | 0.543599 |
| retrieval_pass_count | uint8 | 1 | 1 | 4 | 0.207723 | 4 | 1.93685 | 1.01403 |
| name_token_rank | uint16 | 0 | 0 | 50 | 14.4721 | 45 | 1.17411 | 8.65645 |
| address_token_rank | uint16 | 0 | 4.63391 | 50 | 16.4645 | 45 | 1.96293 | 14.0086 |
| source_balanced_rank | uint16 | 0 | 21.7391 | 50 | 15.7129 | 45 | 2.54003 | 22.6556 |
| heavy_sorted_block | bool | 0 | 0 | 1 | 0.292115 | 2 | 0.0711032 | 0.0946633 |
| target_is_s2 | bool | 0 | 0.769594 | 1 | 0.499974 | 2 | 0.486095 | 0.505475 |
| target_is_s3 | bool | 0 | 0.0773356 | 1 | 0.499974 | 2 | 0.513905 | 0.494525 |
| exact_name_norm | bool | 0 | 0 | 1 | 0.191464 | 2 | 0.293479 | 0.0330519 |
| exact_name_sorted | bool | 0 | 0 | 1 | 0.343172 | 2 | 0.576622 | 0.12764 |
| exact_address_norm | bool | 0 | 0 | 1 | 0.047065 | 2 | 0.0928043 | 0.000425561 |
| exact_name_nosuffix | bool | 0 | 0 | 1 | 0.338805 | 2 | 0.536614 | 0.124279 |
| exact_numeric_set | bool | 0 | 0 | 1 | 0.333912 | 2 | 0.637323 | 0.117747 |
| exact_postal_token | bool | 0 | 0 | 1 | 0.132933 | 2 | 0.0555994 | 0.0172502 |
| country_agreement | bool | 1 | 1 | 1 | 0 | 1 | 1 | 1 |
| s1_name_missing | bool | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| target_name_missing | bool | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| s1_address_missing | bool | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| target_address_missing | bool | 0 | 0 | 1 | 0.135947 | 2 | 0.0371392 | 0.0184739 |
| s1_name_chars | uint16 | 3 | 23 | 66 | 7.64674 | 52 | 23.8909 | 23.565 |
| target_name_chars | uint16 | 1 | 22 | 123 | 8.81856 | 91 | 23.4947 | 23.0672 |
| s1_address_chars | uint16 | 14 | 37.8364 | 187 | 23.6085 | 138 | 48.4794 | 48.32 |
| target_address_chars | uint16 | 0 | 35.375 | 217 | 20.6035 | 223 | 43.6412 | 42.1444 |
| s1_name_tokens | uint16 | 1 | 2 | 9 | 0.940269 | 10 | 2.6472 | 2.63238 |
| target_name_tokens | uint16 | 1 | 2 | 18 | 1.05401 | 20 | 2.78209 | 2.66085 |
| s1_address_tokens | uint16 | 3 | 7 | 26 | 3.60332 | 24 | 8.09534 | 8.09089 |
| target_address_tokens | uint16 | 0 | 6 | 35 | 3.41355 | 36 | 7.47841 | 7.14691 |
| name_char_abs_diff | uint16 | 0 | 6.48274 | 91 | 6.705 | 66 | 3.47079 | 8.08954 |
| address_char_abs_diff | uint16 | 0 | 8.63621 | 187 | 15.6263 | 163 | 8.91751 | 14.1422 |
| name_char_relative_diff | float32 | 0 | 0.252665 | 0.976744 | 0.194666 | 1621 | 0.12828 | 0.279026 |
| address_char_relative_diff | float32 | 0 | 0.197251 | 1 | 0.194037 | 6848 | 0.166543 | 0.23996 |
| name_token_intersection | uint16 | 0 | 0 | 9 | 0.852422 | 11 | 2.14737 | 0.639584 |
| name_token_union | uint16 | 1 | 4.03083 | 20 | 1.81185 | 21 | 3.28192 | 4.65364 |
| name_jaccard | float32 | 0 | 0 | 1 | 0.336768 | 55 | 0.738936 | 0.222838 |
| name_containment_s1 | float32 | 0 | 0 | 1 | 0.35645 | 26 | 0.804209 | 0.270117 |
| name_containment_target | float32 | 0 | 0 | 1 | 0.363582 | 41 | 0.784858 | 0.287973 |
| address_token_intersection | uint16 | 0 | 1 | 25 | 1.74226 | 27 | 6.01018 | 1.19261 |
| address_token_union | uint16 | 3 | 12 | 47 | 5.71029 | 45 | 9.56357 | 14.0452 |
| address_jaccard | float32 | 0 | 0.0837259 | 1 | 0.135768 | 321 | 0.625751 | 0.0906709 |
| address_containment_s1 | float32 | 0 | 0.142851 | 1 | 0.176115 | 185 | 0.731054 | 0.14541 |
| address_containment_target | float32 | 0 | 0.166667 | 1 | 0.188451 | 225 | 0.750568 | 0.164412 |
| name_shared_idf | float32 | 0 | 0 | 1.02968 | 0.00887382 | 4173 | 0.0124762 | 0.00227161 |
| address_shared_idf | float32 | 0 | 0.000732922 | 3.00119 | 0.0220976 | 16265 | 0.0476979 | 0.00361592 |
| shared_address_numbers | uint16 | 0 | 0 | 10 | 0.511315 | 12 | 1.18408 | 0.285647 |
| conflicting_address_numbers | bool | 0 | 1 | 1 | 0.431343 | 2 | 0.242653 | 0.762978 |
| shared_postal_tokens | uint8 | 0 | 0 | 1 | 0.132933 | 2 | 0.0555994 | 0.0172502 |
| conflicting_postal_tokens | bool | 0 | 0 | 1 | 0.0921249 | 2 | 0.00457671 | 0.00863919 |
| digit_sequence_equal | bool | 0 | 0 | 1 | 0.33272 | 2 | 0.620987 | 0.116984 |
| house_number_equal | bool | 0 | 0 | 1 | 0.423779 | 2 | 0.717032 | 0.225092 |
| name_ratio | float32 | 0 | 0.427054 | 1 | 0.219478 | 2077 | 0.821118 | 0.46848 |
| name_partial_ratio | float32 | 0 | 0.533007 | 1 | 0.225988 | 1304 | 0.896577 | 0.568623 |
| name_token_sort_ratio | float32 | 0 | 0.415601 | 1 | 0.221818 | 2021 | 0.835072 | 0.463457 |
| name_token_set_ratio | float32 | 0 | 0.470031 | 1 | 0.255689 | 1699 | 0.896567 | 0.515639 |
| address_ratio | float32 | 0 | 0.395211 | 1 | 0.136327 | 8813 | 0.772193 | 0.408773 |
| address_partial_ratio | float32 | 0 | 0.457922 | 1 | 0.142054 | 5774 | 0.833337 | 0.473312 |
| address_token_sort_ratio | float32 | 0 | 0.413807 | 1 | 0.139414 | 7955 | 0.826956 | 0.425881 |
| address_token_set_ratio | float32 | 0 | 0.430092 | 1 | 0.160172 | 7233 | 0.88411 | 0.450772 |
| s1_script_class | uint8 | 1 | 1 | 1 | 0 | 1 | 1 | 1 |
| target_script_class | uint8 | 0 | 1 | 4 | 0.410668 | 5 | 1.10358 | 1.07119 |
| same_script_class | bool | 0 | 1 | 1 | 0.18929 | 2 | 0.946306 | 0.963111 |
| script_conflict | bool | 0 | 0 | 1 | 0.189007 | 2 | 0.0536285 | 0.0367726 |

## Negative sampling

# Controlled negative-sampling comparison

All recovered positives are retained. Metrics use all baseline-dev candidates.

| Policy | Rows | Positives | Negatives | Neg/pos | Hard/mined | Collision | Random | S2 neg | S3 neg | India rows | US rows | SHA-256 | Macro F0.5 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| current_hybrid | 453,429 | 60,539 | 392,890 | 6.49 | 392,890 | 0 | 0 | 196,544 | 196,346 | 184,452 | 268,977 | `a2e3b7b25fd30d14c7bb44f3a473a866046def583dc0da604396af700c43f8ce` | 0.877791 |
| mixed | 403,808 | 60,539 | 343,269 | 5.67 | 236,487 | 50 | 106,732 | 171,697 | 171,572 | 163,996 | 239,812 | `f5c5db23b35e55f0c9fb466affee205b7faa6b504f2ba1cfc28ba01be0ace3df` | 0.882312 |
| mined | 519,936 | 60,539 | 459,397 | 7.59 | 392,890 | 0 | 66,507 | 229,798 | 229,599 | 211,491 | 308,445 | `a1d6b60028744aa211612268e9b6374c1244893bd9f7e3d591dc829affa21aa2` | 0.882601 |

Selected: **mined**. One mining round only. Training process peak for the combined experiment driver: 1698.2 MiB.

## Feature ablations

| View | Features | Macro F0.5 | Recovered F0.5 | Precision | Recall | India | US | S2 recall | S3 recall | Train s | Infer s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| exact_provenance | 16 | 0.722132 | 0.762078 | 0.871724 | 0.573660 | 0.717561 | 0.725099 | 0.575313 | 0.572096 | 2.75 | 1.58 |
| exact_token | 28 | 0.831784 | 0.880218 | 0.941799 | 0.710538 | 0.804895 | 0.849237 | 0.713874 | 0.707385 | 3.93 | 1.77 |
| exact_token_numeric_address | 49 | 0.853482 | 0.902730 | 0.948359 | 0.748069 | 0.820780 | 0.874708 | 0.752546 | 0.743837 | 5.83 | 2.61 |
| all_non_fuzzy | 53 | 0.857879 | 0.907396 | 0.953482 | 0.751673 | 0.829763 | 0.876129 | 0.756666 | 0.746953 | 6.41 | 2.53 |
| all_v1 | 61 | 0.882601 | 0.932921 | 0.957972 | 0.794182 | 0.853909 | 0.901224 | 0.801695 | 0.787078 | 7.60 | 2.75 |
| without_rank_provenance | 52 | 0.880683 | 0.931109 | 0.956881 | 0.791607 | 0.850549 | 0.900243 | 0.803049 | 0.780789 | 5.56 | 2.56 |
| without_address | 33 | 0.748764 | 0.788681 | 0.905484 | 0.643744 | 0.731220 | 0.760151 | 0.657190 | 0.631031 | 3.85 | 2.52 |
| without_rapidfuzz | 53 | 0.857879 | 0.907396 | 0.953482 | 0.751673 | 0.829763 | 0.876129 | 0.756666 | 0.746953 | 6.41 | 2.53 |

Selected: **all_v1**.

## Learning curve

| Tier | Rows | Positives | Model train s | Macro F0.5 | Precision | Recall | Improvement |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 130,004 | 15,072 | 1.85 | 0.884370 | 0.966283 | 0.787831 |  |
| B | 519,936 | 60,539 | 8.34 | 0.887131 | 0.974052 | 0.778506 | +0.002761 |
| C | 1,300,551 | 151,102 | 21.22 | 0.888010 | 0.970710 | 0.785915 | +0.000879 |

Selected Tier **B**. Tier C improved only 0.000879 over Tier B, so full 1.76M-S1 fitting is not justified.

## Limited configuration comparison

| Configuration | Leaves | Depth | Rounds | Macro F0.5 | Precision | Recall | Train s | Infer s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| leaves15_depth6 | 15 | 6 | 180 | 0.887301 | 0.969367 | 0.788423 | 12.68 | 16.79 |
| leaves31_depth8 | 31 | 8 | 180 | 0.896537 | 0.972402 | 0.802631 | 18.18 | 20.47 |
| leaves31_slow | 31 | 8 | 260 | 0.895296 | 0.972341 | 0.800074 | 25.93 | 26.66 |
| leaves15_fast | 15 | 7 | 120 | 0.887928 | 0.969070 | 0.790679 | 10.03 | 15.21 |

Selected: **leaves31_depth8**.

## Memory stages

- Candidate generation peak: 906.5 MiB; DuckDB 700 MiB, one thread.
- Feature generation peak: 960.9 MiB; 20,000-row Arrow batches.
- Combined development experiments peak: 1698.2 MiB.
- Threshold scoring/search peak: 1678.8 MiB.
- Selected sample matrix: 121.5 MiB; Dataset construction 0.97s; training 12.71s.

## Frozen configuration

```json
{
  "schema_version": 1,
  "status": "development_freeze_not_final_evaluation",
  "candidate_policy_sha256": "46fd324b3d4db7dbdf5fa4b8ce528f681335ce16f759fb93a39ba5b98a040fea",
  "feature_spec_version": "feature_spec_v1.1",
  "feature_spec_sha256": "37982fd6377aa2e36fe1c5dec0484a35c02ba4bcc4e3b67b3a337b33597f6e1f",
  "feature_order": [
    "provenance",
    "retrieved_name_token",
    "retrieved_address_token",
    "retrieval_pass_count",
    "name_token_rank",
    "address_token_rank",
    "source_balanced_rank",
    "heavy_sorted_block",
    "target_is_s2",
    "exact_name_norm",
    "exact_name_sorted",
    "exact_address_norm",
    "exact_name_nosuffix",
    "exact_numeric_set",
    "exact_postal_token",
    "country_agreement",
    "s1_name_missing",
    "target_name_missing",
    "s1_address_missing",
    "target_address_missing",
    "s1_name_chars",
    "target_name_chars",
    "s1_address_chars",
    "target_address_chars",
    "s1_name_tokens",
    "target_name_tokens",
    "s1_address_tokens",
    "target_address_tokens",
    "name_char_abs_diff",
    "address_char_abs_diff",
    "name_char_relative_diff",
    "address_char_relative_diff",
    "name_token_intersection",
    "name_token_union",
    "name_jaccard",
    "name_containment_s1",
    "name_containment_target",
    "address_token_intersection",
    "address_token_union",
    "address_jaccard",
    "address_containment_s1",
    "address_containment_target",
    "name_shared_idf",
    "address_shared_idf",
    "shared_address_numbers",
    "conflicting_address_numbers",
    "conflicting_postal_tokens",
    "digit_sequence_equal",
    "house_number_equal",
    "name_ratio",
    "name_partial_ratio",
    "name_token_sort_ratio",
    "name_token_set_ratio",
    "address_ratio",
    "address_partial_ratio",
    "address_token_sort_ratio",
    "address_token_set_ratio",
    "s1_script_class",
    "target_script_class",
    "same_script_class",
    "script_conflict"
  ],
  "feature_types": [
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32",
    "float32"
  ],
  "removed_redundant_inputs": [
    "retrieved_sorted_name",
    "retrieved_exact_address",
    "shared_postal_tokens",
    "target_is_s3"
  ],
  "training_split": "model_fit tier B",
  "training_split_manifest_sha256": "e597017d0246b01b869b9416f07294a68516d5ecc58fb775a212e36529fcfd56",
  "negative_sampling": {
    "policy": "one_round_mined",
    "sample_sha256": "a1d6b60028744aa211612268e9b6374c1244893bd9f7e3d591dc829affa21aa2",
    "all_recovered_positives": true,
    "mined_false_positives_per_source_s1": 10,
    "random_per_source_s1": 2,
    "rounds": 1
  },
  "lightgbm": {
    "version": "4.7.0",
    "parameters": {
      "learning_rate": 0.05,
      "num_leaves": 31,
      "max_depth": 8,
      "min_data_in_leaf": 100,
      "feature_fraction": 0.9,
      "bagging_fraction": 0.9,
      "bagging_freq": 1,
      "lambda_l2": 1.0,
      "num_threads": 2,
      "objective": "binary",
      "metric": "binary_logloss",
      "seed": 42,
      "feature_fraction_seed": 42,
      "bagging_seed": 42,
      "data_random_seed": 42,
      "deterministic": true,
      "force_col_wise": true
    },
    "rounds": 180,
    "model_format": "LightGBM native text"
  },
  "threshold": 0.61,
  "threshold_selection": "one coarse grid at 0.05 plus one 0.01 fine grid; maximize macro F0.5; lower threshold wins exact ties",
  "set_assembly": {
    "policy": "global_threshold",
    "comparison": ">=",
    "allow_empty": true,
    "allow_multiple": true,
    "force_top_1": false,
    "max_matches": null,
    "numeric_conflict_filter": false,
    "source_specific_thresholds": false,
    "deduplicate_identity": true,
    "order": "source1_entity_id,target_entity_id"
  },
  "inference": {
    "batch_candidates": 200000,
    "threads": 2,
    "matrix_dtype": "float32",
    "identity_columns_excluded_from_matrix": true
  },
  "seeds": {
    "development_split": "model_development_v1_20260926",
    "sample": "model_development_samples_v1_20260926",
    "lightgbm": 42
  }
}
```

## One-time threshold selection

Candidate oracle macro F0.5: **0.949664**; pair ceiling: **0.879083**.
Selected global threshold **0.61**: macro F0.5 **0.896504**, recovered-truth F0.5 **0.944678**, precision **0.971159**, recall **0.804032**, singleton accuracy **0.892308**, mean predictions **2.864**, empty rate **0.079577**.
India/US macro: 0.878018/0.908927; S2/S3 recall: 0.807339/0.800928; address present/missing recall: 0.828493/0.274941.
Tune-only S2/S3 optimal thresholds were both 0.70; source-specific thresholds were ineligible. Numeric-conflict rejection scored 0.781608 and was rejected.

## Reproducibility

Representative 300,238-row feature checksum matched exactly. Two fresh model loads had max score difference 0.0, identical decisions and prediction sets, and predictions were a subset of candidates.

## Full-scale projection

```json
{
  "chosen_training_sample": {
    "rows": 519936,
    "matrix_bytes": 127384320,
    "sample_bytes": 18344651,
    "training_seconds": 12.714578628540039
  },
  "model_threshold_observed": {
    "s1": 55091,
    "candidates": 8621016,
    "feature_bytes": 289960971,
    "score_bytes": 102078146,
    "candidate_seconds": 421.318,
    "feature_seconds": 656.0,
    "inference_seconds": 21.462211847305298,
    "peak_rss_bytes": 1760399360
  },
  "model_final_eval_projected": {
    "s1": 110341,
    "candidates": 17266913,
    "feature_bytes": 580758808,
    "score_bytes": 204450903,
    "wall_seconds": 2201
  },
  "test_projected": {
    "s1": 1732544,
    "candidates": 271582123,
    "candidate_identity_provenance_bytes": 2399309219,
    "feature_bytes": 9134447273,
    "score_bytes": 3215699820,
    "candidate_hours": 3.686805621225632,
    "feature_hours": 5.740425254852664,
    "inference_minutes": 11.268495998164882,
    "output_grouping_minutes": 2.8171239995412205
  },
  "in_memory": {
    "batch_candidates": 200000,
    "float32_matrix_bytes": 48800000,
    "score_bytes": 800000,
    "expected_process_peak_bytes": 1800000000
  },
  "restart_partitions": {
    "train": 128,
    "final_eval": 64,
    "test": 96
  },
  "temp_disk_recommendation_gb": {
    "final_eval": 8,
    "test": 48
  },
  "model_load_overhead": "below one second; 645 KB native text model"
}
```
