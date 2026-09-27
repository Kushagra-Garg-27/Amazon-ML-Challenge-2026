# V2 matcher and decision sprint — research only

Brief: `C:/Users/kusha/.codex/attachments/d03d293a-d7f8-47b3-bd66-e7d94364b5d9/Pasted text.txt`.
Outputs: `work/v2_matcher_sprint_r1`. V1 and earlier research artifacts remain frozen.

1. Recompute frozen scores and baseline: COMPLETE; 0.8952605447038198 V1, 0.9017828798414517 plus_all.
2. Allocate internal research roles by salted SHA-256 of S1: 60% train, 15% model selection, 10% policy/calibration selection, 15% final sprint assessment. These are subsets of the authorized research population, not the sealed V2 populations. Assessment labels stay out of all selection. Prior research exposure means this assessment is not an untouched challenge holdout.
3. Build tested deterministic feature groups and bounded opposite-source anchors from frozen scores. Fit no feature statistics using validation outcomes. Materialize all validation candidates and sampled training negatives, retaining every retrieved training positive. All true training positives remain in the coverage audit, including unavailable candidate positives.
4. Analyze training-only error separation before model search. Compare frozen V1, authorized refit V1 features with uniform negatives, hard negatives, name groups, address, script, cross-source, entity context, and compact combined features.
5. Freeze feature order, compare a small LightGBM search and installed XGBoost. Select using end-to-end model-selection macro F0.5. Use early stopping and record selection optimism.
6. Tune deterministic set policies, Platt/isotonic calibration, and target conflict resolution on the internal policy subset only. Freeze the complete artifact before one final internal assessment. Never iterate from assessment outcomes.
7. Reproduce winning predictions, audit access and resources, execute regression and V1 compatibility tests, publish report, feature specification, policy, model, append-only experiment ledger, hashes, and exact reproduction command.

## Live implementation record

- Feature materialization complete: 8,521,051 rows, 186,049 retrieved training positives,
  100% retrieved-positive coverage, 1,290.6 seconds, observed peak RSS 1.61 GB.
- Training-only error distributions complete. Strong-evidence misses have much more
  missing address evidence and lower cross-source support than accepted true pairs.
- Model comparisons are running. Frozen V1 and every refit use the same complete
  2,828,075 model-selection candidate pairs; no assessment labels have been opened.
- Independent source review corrected feature-complexity selection, all-S1
  under/overprediction rates, and frozen model/feature/policy hash enforcement.
- A bounded, portable entity-utility forest was added to the planned policy search.
  It fits only even-indexed policy S1s and selects on odd-indexed S1s. Target conflicts
  are also resolved only among odd S1s during that selection comparison.
- Frozen-model replay from 100,000-row samples of each new train/selection matrix
  reproduced every stored score exactly. The weak uniform-negative refit is an
  observed experiment, not a feature-order/scoring mismatch.

### Entity-decision extension, before policy outcomes

Wider LightGBM reached 0.919318 on model selection, below the 0.93 milestone.
The policy search therefore compares utility fitting on even policy S1s alone
against fitting on those S1s plus model-selection S1s. Model-selection scores are
out of pair-model training, although the pair hyperparameters were selected on
that cohort. This is a meta-training use of previously exposed development data,
not an independent evaluation claim. Odd policy S1s remain disjoint selection
data for all meta-learners; final sprint assessment remains unused. Calibration
still fits only even policy S1s. This extension was specified before reading any
policy or assessment outcomes and keeps existing artifacts unchanged.

### Bounded pair-model extension, before policy outcomes

Compare one full-context (100-column) model to test interactions omitted by
univariate reduction, and one entity-balanced training objective. The latter
weights positives inversely by complete truth count and negatives inversely by
sampled negative count, then normalizes each class's mean weight to one. Both use
the measured wider LightGBM settings and early stopping. Up to nine cached-score
blends of the strongest native model with three complementary models are compared.
An ensemble must gain at least 0.001 over a native alternative to survive the
complexity rule. Original selection files remain immutable; a separate extension
manifest records any revision, and the policy binds its exact manifest filename
and hash before assessment.

### Residual score correction, after initial policy selection

The full-context model plus best scalar policy reached 0.922415 on odd policy
entities, below 0.93. Initial calibration, utility, context and conflict searches
did not yield a retained complex rule. Before opening assessment, two residual
LightGBM models (depth 4/6) therefore fit model-selection S1s using base logits as
initial predictions. Inputs add nine label-free current-model score summaries.
All positive pairs and score>=0.05 negatives are kept; easy 1/16 negatives receive
weight 16. Even policy S1s choose early stopping; odd policy S1s choose thresholds.
This reuses exposed development data and is explicitly selection, not independent
evaluation. Original policy remains immutable; any >=0.001 gain is recorded in
`decision_policy_refined.json`, otherwise rejected. Assessment is still unopened.

### Final bounded capacity check

Residual depth 6 reached 0.923448 on odd policy S1s, +0.001033 over the original
decision, so its SHA-bound artifact was retained before assessment. Two larger
native models (127/255 leaves, depth 12/14, at most 850 trees, early stopping)
now test remaining pair-model underfit using the original train/model-selection
roles and full 100-feature context. A small raw-score threshold/source/missing/
expected-set/conflict comparison uses odd policy S1s. It must improve the current
policy by >=0.001 to be retained; otherwise the residual policy remains active.
No final-assessment label is used to make this extension or select its outcome.

Access: use `research_only`, never `populations`. No sealed membership/labels, historical row-level training labels, test rows, submission outputs, external enrichment, or internet access. The prior sealed-membership incident stays documented; no new access is authorized by it.

Rulings: The brief's description of V1 as trained on V2 populations conflicts with frozen policy metadata (`model_fit tier B`); reuse the frozen model, and train all new models solely on internal research-train. The fixed plus_all oracle is 0.958075 across all research S1; any claimed score above its appropriate subset oracle is an error. Tiny costly gains are rejected unless an ablation supports a downstream contribution. Existing research branch and data checkout are reused because copying untracked multi-GB artifacts would impair reproducibility.
