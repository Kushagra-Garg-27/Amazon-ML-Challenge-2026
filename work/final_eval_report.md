# Frozen matcher final evaluation

Release gate: **PASS**. Fixed threshold: **0.61**. Configuration SHA-256: `17165a4c083e73c747d5b57ca9e758959b0a8609d7306d738ad7ecb97326c6e9`.

## Pre-open firewall

All frozen checksums and the 61-feature order matched; 58 development artifacts had zero final-evaluation S1 overlap. Prior cross-split labelled-target overlap was zero. The historical split creation report documents transient GT aggregates that were removed and not used for selection.

## Candidates, features and predictions

Candidates: 17,295,784; duplicates: 0; zero-candidate S1: 108. Features: 17,295,784; added/removed: 0/0. Accepted pairs: 315,987. Prediction sets: 110,341; empty: 8,895.

Candidate counts per S1: mean 156.75, median 180, p90 200, p95 201, p99 212, maximum 293. S2/S3 candidate counts: 8,728,137 / 8,567,647. All 16 candidate parts were checksummed; no partition retry was needed.

## Metrics

Candidate oracle macro F0.5: **0.949598**; candidate pair recall: **0.879015**.
Frozen matcher macro F0.5: **0.896256**; pair precision: **0.970847**; pair recall: **0.803379**; singleton accuracy: **0.893872**.
India/US macro F0.5: 0.877142/0.909125.

## Bootstrap intervals and threshold comparison

| Metric | Final | 95% S1 bootstrap interval | Threshold reference | Difference |
|---|---:|---:|---:|---:|
| macro_f0_5 | 0.896256 | [0.895048, 0.897387] | 0.896504 | -0.000248 |
| singleton_accuracy | 0.893872 | [0.886762, 0.901773] | 0.892308 | +0.001565 |
| india_macro_f0_5 | 0.877142 | [0.875012, 0.879106] | 0.878018 | -0.000875 |
| us_macro_f0_5 | 0.909125 | [0.907654, 0.910435] | 0.908927 | +0.000198 |

## Predeclared slices and errors

Country, source, address availability, script relation, candidate provenance, candidate rank, and truth-count bands are recorded in `final_eval_metrics.json`.

| Predeclared pair slice | Precision | Recall |
|---|---:|---:|
| S2 | 0.971292 | 0.807622 |
| S3 | 0.970426 | 0.799397 |
| Both addresses present | 0.974798 | 0.828173 |
| Either address missing | 0.765740 | 0.269730 |
| Script conflict | 0.908648 | 0.516320 |
| Same or empty script class | 0.974122 | 0.825934 |

For provenance and candidate-rank bands, `final_eval_metrics.json` reports precision and recall among recovered truths; those candidate-conditioned bands cannot include truths that retrieval missed. The same file records the predeclared 0, 1, 2, 3–4, and 5+ true-match-count bands.

Unseen sorted-name S1: 60,608 (India 21,365; US 39,243); oracle macro F0.5 0.965457, matcher macro F0.5 0.918107, pair precision/recall 0.975436/0.838433. The stricter unseen-name-and-address slice contains 57,640 S1 with oracle 0.965553 and matcher 0.920556. These are descriptive stress slices, not model-selection results.
Historical grouped validation overlap: model_fit 167,878, model_tune 5,554, model_threshold 5,701, baseline_dev 0. It was not scored as an independent matcher evaluation.

Candidate misses: 46,199; matcher false negatives: 28,882; matcher false positives: 9,212. Singleton false-positive S1s: 672; multi-match underprediction S1s: 44,738; overprediction S1s: 4,337.

## Release criteria

- all_integrity_invariants: PASS
- macro_f0_5_at_least_0_88: PASS
- macro_drop_at_most_0_02: PASS
- pair_precision_at_least_0_95: PASS
- country_drop_at_most_0_04: PASS
- predictions_subset_of_candidates: PASS
- zero_duplicate_predictions: PASS
- one_prediction_set_per_s1: PASS

## Resources

```json
{
  "release_gate_config_sha256": "17165a4c083e73c747d5b57ca9e758959b0a8609d7306d738ad7ecb97326c6e9",
  "candidate": {
    "wall_seconds": 1086.9553117752075,
    "bytes": 152720150,
    "failed_retried_partitions": [],
    "exit_code": 0,
    "peak_process_rss_bytes": 1037881344,
    "peak_temp_bytes": 346816512
  },
  "feature": {
    "wall_seconds": 1253.2509050369263,
    "bytes": 582642896,
    "bytes_per_candidate": 33.68698961550399,
    "peak_process_rss_bytes": 1195008000,
    "peak_temp_bytes": 282624000,
    "restart_events": 2,
    "failed_retried_partitions": [
      {
        "part": "model_final_eval_02_india",
        "failure": "DuckDB OOM at 700MB unfiltered join",
        "retry": "900MB filtered join succeeded"
      },
      {
        "part": "model_final_eval_00_us",
        "failure": "DuckDB OOM at 900MB unfiltered and 700MB filtered joins",
        "retry": "900MB filtered join succeeded"
      }
    ],
    "workspace_interruption": "Feature output directory disappeared during concurrent Git initialization; regenerated from immutable candidates",
    "wall_seconds_scope": "successful full retry, including resume and structural audit; earlier failed attempt logs were removed by workspace change"
  },
  "score": {
    "wall_seconds": 86.27948069572449,
    "bytes": 204266002,
    "peak_process_rss_bytes": 1492795392
  },
  "evaluation_wall_seconds": 90.07389736175537,
  "evaluation_peak_process_rss_bytes": 1123590144
}
```

No model, feature definition, candidate policy, or threshold was changed after the final-evaluation labels were opened.

The [test-inference runbook](test_inference_runbook.md) and resource plan were prepared without running test inference. The runbook names the test-specific rank, feature-key, score, and assembly interfaces that still require implementation and fixture verification before its planned commands can run.
