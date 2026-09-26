# Matcher split v1

Seed: `matcher_split_v1_20260926`. Candidate development remains unchanged; the untouched 90% is partitioned exactly by deterministic MD5 order.

| Split | S1 | GT links | Singletons | ID SHA-256 |
|---|---:|---:|---:|---|
| candidate_dev | 220,531 | 763919 | 12277 | `85f683d4a2f7e72dc2c7dd1b17f021fd735711bc8e14cc38d4c687ab04ada7c4` |
| model_train | 1,765,608 | 6110289 | 98558 | `d7eed12878b7cd5281173ba6e216495138f5f27777a95b5e8d0e27e7cb79ea88` |
| model_calibration | 110,341 | 382301 | 6080 | `2b96a53193f6dbb68c7b0de0424c310f95d097f67b770944fcb84d2b736d89ca` |
| model_final_eval | 110,341 | firewalled | firewalled | `256c997695fe214bb055f2f74faa097c929db22ebcf7ff8bb37707cab382aaac` |

S1 duplicates/overlap: 0. Labelled target IDs crossing splits: 0.

The persisted `model_final_eval` record contains membership count and ID checksum only. During the initial split audit, GT-link and singleton aggregates were inadvertently computed before the stricter firewall was applied; they were removed and never used for features, training, scoring, thresholds, or model comparison. No features, labels, predictions, or model metrics were materialized for this split.

## Aggregate distributions

```json
{
  "candidate_dev": {
    "s1": 220531,
    "gt_links": 763919,
    "singletons": 12277,
    "mean_links": 3.463998258748203,
    "country": {
      "india": 87862,
      "us": 132669
    },
    "match_count_distribution": {
      "0": 12277,
      "1": 11932,
      "2": 37164,
      "3": 53144,
      "4": 48592,
      "5": 32189,
      "6": 16513,
      "7": 6439,
      "8": 1830,
      "9": 391,
      "10": 56,
      "11": 4
    },
    "entity_id_sha256": "85f683d4a2f7e72dc2c7dd1b17f021fd735711bc8e14cc38d4c687ab04ada7c4"
  },
  "model_train": {
    "s1": 1765608,
    "gt_links": 6110289,
    "singletons": 98558,
    "mean_links": 3.460727975858741,
    "country": {
      "india": 706984,
      "us": 1058624
    },
    "match_count_distribution": {
      "0": 98558,
      "1": 95285,
      "2": 300529,
      "3": 424972,
      "4": 386971,
      "5": 257665,
      "6": 131673,
      "7": 51165,
      "8": 14964,
      "9": 3376,
      "10": 418,
      "11": 32
    },
    "entity_id_sha256": "d7eed12878b7cd5281173ba6e216495138f5f27777a95b5e8d0e27e7cb79ea88"
  },
  "model_calibration": {
    "s1": 110341,
    "gt_links": 382301,
    "singletons": 6080,
    "mean_links": 3.464722995078892,
    "country": {
      "india": 43945,
      "us": 66396
    },
    "match_count_distribution": {
      "0": 6080,
      "1": 5920,
      "2": 18912,
      "3": 26466,
      "4": 24230,
      "5": 16002,
      "6": 8385,
      "7": 3143,
      "8": 946,
      "9": 220,
      "10": 37
    },
    "entity_id_sha256": "2b96a53193f6dbb68c7b0de0424c310f95d097f67b770944fcb84d2b736d89ca"
  },
  "model_final_eval": {
    "s1": 110341,
    "entity_id_sha256": "256c997695fe214bb055f2f74faa097c929db22ebcf7ff8bb37707cab382aaac",
    "firewall": "membership and ID checksum only; no label aggregates persisted"
  }
}
```
