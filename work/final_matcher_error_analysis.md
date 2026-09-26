# Frozen matcher error analysis

Threshold: `0.61`. Candidate-generation losses and matcher errors are disjoint.

## baseline_dev

- Candidate-generation loss: 4,364
- Matcher false negatives among recovered candidates: 2,651
- Matcher false positives: 809
- singleton false positive s1: 47
- complete recovery s1: 5,043
- partial multi match s1: 4,098
- overprediction s1: 489
- empty prediction error s1: 304

Detailed country, source, provenance, rank, address, script, exact-evidence, numeric-conflict, and true-link-count aggregates are in `work/final_matcher_error_analysis.json`.

## model_tune

- Candidate-generation loss: 22,736
- Matcher false negatives among recovered candidates: 14,419
- Matcher false positives: 4,463
- singleton false positive s1: 317
- complete recovery s1: 28,074
- partial multi match s1: 21,984
- overprediction s1: 2,699
- empty prediction error s1: 1,605

Detailed country, source, provenance, rank, address, script, exact-evidence, numeric-conflict, and true-link-count aggregates are in `work/final_matcher_error_analysis.json`.
