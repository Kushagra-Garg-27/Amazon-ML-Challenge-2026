"""Sprint evidence packaging from recorded aggregates and frozen model artifacts."""
import json
import platform
from pathlib import Path
import importlib.metadata
from .data import ROOT,OUT,V1,FEATURES,GROUPS,write_once
from .access import sha
from .experiments import active_policy_path

def load(name):return json.loads((OUT/name).read_text())

def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
      ['| '+' | '.join(str(v) for v in row)+' |' for row in rows])

def f(x):return 'unavailable' if x is None else f'{x:.6f}'

def run():
    assessment=load('assessment.json');policy=load(active_policy_path().name);selected=load(policy.get('model_manifest_file','selected_model.json'))
    errors=load('error_analysis.json');search=load('policy_search.json');baseline=load('baseline.json')
    reproduction=load('reproduction.json');rows=[json.loads(x) for x in (OUT/'experiments.jsonl').read_text().splitlines()]
    resources={p.stem:json.loads(p.read_text()) for p in OUT.glob('*_resources.json')}
    spec=load('feature_spec.json');lines=['# V2 matcher and decision sprint — measured research report','']
    add=lines.append;m=assessment['metrics'];b=assessment['frozen_v1_baseline'];ci=assessment['paired_vs_frozen']
    add(f'Best frozen V2 architecture on the final internal research assessment: **macro F0.5 {f(m["macro_f05"])}**, compared with frozen V1 on the same candidates/S1s at **{f(b["macro_f05"])}**. Paired delta **{f(ci["delta"])}**, 95% CI **[{f(ci["ci95"][0])}, {f(ci["ci95"][1])}]**. The 0.93 milestone is {"reached on this internal split" if m["macro_f05"]>=.93 else "not reached"}.')
    add('')
    add('These results are from an S1 split inside the previously used research population. They are not an untouched challenge holdout or a full-100,000-S1 out-of-fold result. Primary pair models fit train S1s and select on model-selection S1s. Later utility/residual learners reuse model-selection and/or even policy S1s for fitting or early stopping; odd policy S1s select decisions. Final sprint assessment S1s remain excluded from these fitting/selection steps. Earlier retrieval experiments exposed aggregate research outcomes, so independent generalization remains unverified. No result claims 0.99 feasibility.')
    add('')
    add('## Frozen baseline and candidate boundary')
    add('')
    add(f'The frozen model was freshly replayed before V2 implementation. V1 candidates reproduced {baseline["v1"]["macro_f05"]:.10f}; `v1_plus_all` reproduced {baseline["v1_plus_all"]["macro_f05"]:.10f}. Experiments use role-specific slices of the same fixed 18,895,613 research candidate pairs, with training-negative sampling only. The full-research candidate oracle is 0.958075; the final internal subset oracle is {f(m["oracle_macro_f05"])}. A matcher cannot exceed the corresponding fixed-candidate oracle.')
    add('')
    add('The frozen policy identifies its original fit as `model_fit tier B`, not the newly allocated V2 training population. Historical row-level training data was not reopened. The “V1 feature refit” comparison below trains on authorized internal research-train S1s and is labeled accordingly.')
    add('Fresh baseline score partitions are byte-identical to all 32 frozen Phase 2 score partitions (`baseline_reconstruction.json`). `baseline_rates_all_s1.json` clarifies all-S1 under/overprediction rates; the initial supplement used multimatch-only rates and is preserved unchanged.')
    add('')
    add('## Internal protocol')
    add('')
    add('S1 roles were chosen by salted SHA-256 before V2 feature analysis: 59,856 train; 14,969 model selection; 10,119 policy/calibration; 15,056 final internal assessment. Within the policy role, even entity indices fit calibration and odd indices select decisions. Every stage asserts positive membership in the 100,000 authorized S1s. It never decodes a sealed membership file.')
    add('')
    add('Training negatives combine deterministic 1/16 sampling and all frozen V1 scores ≥0.05; every candidate-recovered training positive is retained. Positives absent from the fixed candidates remain in the metric truth denominator. Validation candidate sets are complete. Early stopping optimizes per-S1 macro F0.5 at 0.61, with a bounded model search. Features use record attributes, target-only DF, frozen scores and at most two accepted opposite-source anchors per candidate. No GT target assignments construct a graph or feature.')
    add('')
    add('## Model and feature ablations')
    add('')
    models=[x for x in rows if x['status']=='MODEL_SELECTION_ONLY']
    add(table(['Experiment','Features','Selection F0.5','Precision','Recall','Singleton','Runtime s','Peak RSS GB'],
      [(x['experiment_id'],len(x['feature_set']),f(x['metrics']['macro_f05']),f(x['metrics']['precision']),
        f(x['metrics']['recall']),f(x['metrics']['singleton_f05']),round(x['runtime'],1),
        round(x['peak_RSS']/1e9,2)) for x in models]))
    add('')
    add('Every row uses full model-selection candidate sets; these comparisons are selection estimates, not independent claims. The append-only `experiments.jsonl` includes candidate oracle/recall, source/address/script slices, under/overprediction, artifact SHA, resource scope and status. `error_analysis.json` contains training-only distributions for all requested error classes. Nonredundant features were selected only from training separation and correlation, then tested through end-to-end ablations.')
    add('Two bounded follow-ups tested all 100 features (including conditional context signals omitted by univariate reduction) and entity-balanced training weights. Up to nine cached two-model blends were also measured; native training/inference costs are additional to the blend-row runtimes. Original selection files remain immutable; an extension manifest records the final choice. The policy binds the selected manifest filename and SHA. Ensembles with less than 0.001 gain over a native alternative were rejected.')
    add('')
    add(table(['Error class','Sampled candidate pairs','S1 entities'],[(k,v['pairs'],v['s1']) for k,v in errors['classes'].items()]))
    add('')
    add(table(['Signal mean','Accepted TP','Strong-evidence FN','Accepted FP'],
      [(name,*[f(errors['classes'][kind]['distributions'][name]['mean']) for kind in ('C_TP','A_strong_evidence_FN','B_all_FP')])
       for name in ('name_ratio','address_ratio','number_jaccard','translit_ratio','cross_joint','cross_support_count','addr_missing','source_balanced_rank')]))
    add('')
    add('The error table identifies missing/weak address evidence and reduced cross-source support among strong-evidence false negatives. Separation is descriptive; only the ablations establish whether adding a signal improves the actual metric. `docs/v2_matcher_feature_spec.md` supplies formulas, missing behavior and the limitations of every added feature; the frozen V1 definitions remain in `work/feature_spec_v1_1.json`.')
    add('')
    add('Error distributions use all retrieved training positives and hard negatives plus the random negative sample; easy-negative distributions are not population-weighted estimates. The initial source-only/multisource classes use candidate-recovered training truth. `error_analysis_complete_truth.json` supplements these with complete-training-truth source classes and all-S1 under/overprediction counts, including retrieval misses. Numeric/locality features are observable token proxies; no city database, geocoding or identity enrichment was used. Devanagari romanization is a limited deterministic local heuristic, not a general translation system.')
    add('')
    add('## Calibration, entity decisions and global consistency')
    add('')
    ranking=load('selection_ranking_diagnostics.json')
    add(f'Development ranking diagnostic: best scalar threshold F0.5 {f(ranking["best_global_selection"]["macro_f05"])}, optimistic per-entity score-threshold oracle {f(ranking["oracle_per_entity_score_threshold"])}. The latter chooses a threshold with knowledge of each entity’s labels and is diagnostic only, never an inference policy. It separates decision headroom from fixed candidate/ranking limitations.')
    add('')
    add(f'Selected model: `{selected["experiment_id"]}`. Calibration: `{policy["calibration"]}`. Frozen decision: `{json.dumps(policy["decision"],sort_keys=True)}`. Candidate-count/cross-support thresholds, missing-address thresholds, separate source thresholds, score-gap/singleton rules, and expected-set selection were compared. Complex rules were rejected when their measured gain over the best global threshold was below 0.001.')
    add('')
    add(table(['Calibration','Brier on internal calibration holdback'],[(k,f(v['brier'])) for k,v in search['calibration_metrics'].items()]))
    add('')
    add('Calibration curves and every searched policy appear in `policy_search.json`. Target conflict resolution uses model confidence with a stable S1 tie-break. Namespaced targets make source-local versus global target uniqueness equivalent here because S1 capacity remains unrestricted; enforcing one total match per S1 would contradict multi-match evaluation. Uniqueness is tested as a decision variant, not assumed from GT. Conflict results on a 15k/10k research role do not establish behavior among all 1.73M test S1s.')
    add('A bounded entity-utility forest also predicts the per-S1 F0.5 contribution of 15 possible thresholds, including an empty-set choice. Inputs are label-free score, source, address and cross-support summaries. Fitting on even policy entities alone is compared with adding model-selection entities, whose scores are out of pair-model training but whose labels previously selected pair hyperparameters. This documented meta-training extension was specified before policy outcomes. Odd policy entities select the utility model and compare it with simpler rules; assessment entities remain excluded. Numerical trees are portable and SHA-bound to the selected policy. A synthetic test confirms agreement with scikit-learn.')
    add('')
    add('## Final internal assessment')
    add('')
    if (OUT/'residual_search.json').exists():
        residual=load('residual_search.json')
        add(f'After initial policy selection remained below 0.93, a bounded residual correction experiment fit model-selection entities, early-stopped on even policy entities, and selected thresholds on odd policy entities. Its best development F0.5 was {f(residual["best"]["metrics"]["macro_f05"])}; verdict `{residual["status"]}`. It uses original features plus nine label-free current-score context values, with base logits as initial predictions. These reused development outcomes introduce selection optimism; assessment was still unopened. Original policy and every rejected result remain preserved.')
        add('')
    if (OUT/'capacity_search.json').exists():
        capacity=load('capacity_search.json')['result']
        add(f'A final bounded capacity check tested 127/255-leaf native models with early stopping, followed by a small threshold/source/missing/expected-set/conflict search. Its best policy-selection F0.5 was {f(capacity["metrics"]["macro_f05"])}; retained: {capacity["retained"]}. The >=0.001 complexity rule was applied against the previously retained policy. These are adaptively reused development estimates, not independent generalization results.')
        add('')
    add(table(['Metric','Frozen V1','Selected V2'],[(key,f(b.get(key)),f(m.get(key))) for key in (
      'macro_f05','precision','recall','candidate_pair_recall','oracle_macro_f05','singleton_f05',
      'empty_prediction_rate','average_predictions_per_s1','underprediction_rate','overprediction_rate')]))
    add('')
    for key in ('source_slices','address_slices','script_slices'):
        add(table([key,'Truth links','TP','Precision','Recall'],
          [(str(value),data['truth'],data['tp'],f(data['precision']),f(data['recall'])) for value,data in m[key].items()]))
        add('')
    add('Source true means S2 and false means S3; address true means either address missing; script true means conflicting nonempty script classes. The full truth denominators include unretrieved links. A source-only name similarity boost that does not survive precision weighting is not considered a success.')
    add('')
    add('## Access, resources and reproducibility')
    add('')
    add('The earlier Phase 2 sealed-membership incident remains recorded in `work/v2_access_ledger.jsonl`. This sprint reads only research membership, research labels restricted by internal role, inference-available training record attributes, frozen V1 artifacts, and aggregate previous reports. No sealed membership, sealed label, real test row, submission output, external enrichment, or future-derived statistic is opened. Target DF is computed from observable target records; no validation label enters a feature statistic. The frozen teacher was trained before this sprint and never refit on validation labels.')
    add('')
    add(table(['Stage','Wall seconds','Sampled peak RSS GB','Peak temporary GB'],
      [(name,round(x['wall_seconds'],1),round(x['sampled_peak_process_tree_rss_bytes']/1e9,2),
        round(x['sampled_peak_temp_disk_bytes']/1e9,2)) for name,x in resources.items()]))
    add('')
    add('Peak RSS above the preferred 1.8GB threshold, where observed, is reported rather than hidden; dense matrices use float32 and disk-backed NumPy arrays, DuckDB uses 1GB and bounded spill. No O(N²) target matrix is built. Full-test candidate density remains 327,374,809; V2 feature/training/scoring costs have not been measured on test and are not a production runtime certification.')
    add('The early prepare/features resource receipts retain the reused monitor’s historical 700MB/one-thread labels. Actual V2 DuckDB connection settings are 1,000MB and two threads; later receipts record these settings correctly. Peak RSS and wall-time measurements are observed values.')
    feature_manifest=load('features/manifest.json')
    projected_feature=feature_manifest['wall_seconds']/feature_manifest['rows']*327374809
    projected_score=assessment['runtime']/len(__import__('numpy').load(OUT/'assessment_raw_scores.npy',mmap_mode='r'))*327374809
    add(f'Exploratory full-stack feature generation projects to {projected_feature/3600:.2f} hours for 327.4M pairs using observed research wall-time per materialized row; selected-model scoring plus decision projects to {projected_score/3600:.2f} hours using the assessment runtime. These estimates exclude frozen V1 features/scores and target preparation, and the final selected feature subset may be cheaper. They are not a claim of satisfying the older 1.5× runtime gate.')
    add('')
    tests=load('test_results_final.json') if (OUT/'test_results_final.json').exists() else load('test_results.json')
    add(f'Compatibility verification: {tests["v1"]["passed"]} V1 assertions passed; {len(tests["v1"]["skipped"])} historical-label assertions were explicitly skipped. {tests["v2"]["passed"]} V2 tests passed. This is not reported as an unrestricted full V1 suite run. `leakage_audit.json` records the integrity checks and the prior membership incident; application-level assertions are not an OS-enforced file-read trace.')
    add('')
    add(f'Reproduction: `{reproduction["command"]}`. The independent rerun compares every assessment raw score, decision score and accepted decision exactly and recalculates macro F0.5; status **{reproduction["status"]}**. It performs no retraining or tuning. Execution sequence: frozen baseline replay and audit; sprint `prepare`, `features`, `errors`, `train`; `scripts/v2_matcher_extend.py`; `scripts/v2_matcher_selection_diagnostics.py`; sprint `decision`; `scripts/v2_matcher_residual_search.py`; `scripts/v2_matcher_capacity.py`; sprint `assessment`, `reproduce`; `scripts/v2_matcher_audit.py`; compatibility tests; sprint `report`. The complete-truth error supplement and baseline rate clarification are separate diagnostic scripts. Existing results are immutable; a from-scratch experiment requires a separately versioned output root.')
    add('')
    add('## Research recommendation')
    add('')
    add('Retain only changes supported by the measured selection and final assessment evidence. The current results identify the observed residual matcher/decision gap within a fixed retrieval oracle; they do not authorize sealed-population evaluation or production inference. If 0.93 was not reached, report that shortfall explicitly and use training/model-selection diagnostics to design a separately versioned next experiment. Do not iterate from the final internal assessment.')
    (OUT/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    packages={name:importlib.metadata.version(name) for name in ('numpy','duckdb','pyarrow','rapidfuzz','lightgbm','xgboost','scikit-learn')}
    files=[]
    for p in sorted(OUT.rglob('*')):
        if p.is_file() and 'tmp' not in p.relative_to(OUT).parts and p.name!='reproducibility_manifest.json':
            files.append({'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p),'bytes':p.stat().st_size})
    code_paths=list((ROOT/'code/business_entity_resolution/src/er/matcher_v2').glob('*.py'))+list((ROOT/'scripts').glob('v2_matcher*.py'))+list((ROOT/'tests_v2').glob('test_*sprint*.py'))+[ROOT/'tests_v2/test_entity_utility.py',ROOT/'tests_v2/test_residual.py',ROOT/'docs/v2_matcher_feature_spec.md']
    code=[{'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p)} for p in sorted(code_paths)]
    write_once(OUT/'reproducibility_manifest.json',{'files':files,'code':code,'python':platform.python_version(),
      'packages':packages,'candidate_policy':'v1_plus_all','population':'v2_candidate_research',
      'selected_model':selected['experiment_id'],'model_SHA':selected['artifact_SHA'],
      'decision_file':active_policy_path().name,'decision_SHA':sha(active_policy_path()),'feature_spec_SHA':sha(OUT/'feature_spec.json'),
      'reproduce_command':reproduction['command'],'test_data_accessed':False,'sealed_membership_read':False})
    print('report',OUT/'report.md','macro F05',m['macro_f05'],flush=True)
