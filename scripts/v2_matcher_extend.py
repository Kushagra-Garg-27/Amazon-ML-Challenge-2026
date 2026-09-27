"""Two prespecified follow-ups while model-selection results remain below 0.93."""
from pathlib import Path
import sys,json,time
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.matcher_v2.data import OUT,FEATURES,write_once,ledger
from er.matcher_v2.experiments import role_data,fit_one,select_simple_model,metrics
from er.matcher_v2.evaluate import paired
from er.matcher_v2.access import sha,research_ids,assert_authorized
from er.candidates_v2.runtime import Monitor

if (OUT/'decision_policy.json').exists() or (OUT/'assessment.json').exists():
    raise PermissionError('Pair-model extension must precede policy selection and assessment')
if (OUT/'selected_model_extension.json').exists():raise FileExistsError('Extension already frozen')
prior=json.loads((OUT/'selected_model.json').read_text())
with Monitor(OUT/'tmp') as monitor:
    train=role_data(0);valid=role_data(1)
    params={'num_leaves':63,'max_depth':10,'min_data_in_leaf':50,'lambda_l2':2.}
    full=fit_one('v2_full_context',FEATURES,train,valid,params,rounds=550)
    weighted=fit_one('v2_entity_balanced',prior['feature_set'],train,valid,params,rounds=550,entity_balanced=True)
    wider=json.loads((OUT/'models/v2_wider/result.json').read_text())
    native=list({r['experiment_id']:r for r in (prior,wider,full,weighted)}.values())
    native_best=max(native,key=lambda r:r['metrics']['macro_f05'])
    alternatives=[json.loads((OUT/'models'/name/'result.json').read_text()) for name in ('v2_xgboost','v2_names_reduced')]
    alternatives += [r for r in native if r['experiment_id']!=native_best['experiment_id']]
    # Three most promising distinct complementary native models, three weights.
    alternatives=sorted({r['experiment_id']:r for r in alternatives if r['experiment_id']!=native_best['experiment_id']}.values(),key=lambda r:-r['metrics']['macro_f05'])[:3]
    results=list(native);_,baseline_f=metrics(valid,valid['baseline'])
    allowed=set(research_ids(ROOT));valid_ids=set(valid['meta']['source1_entity_id'].to_pylist())
    primary=np.load(OUT/'models'/native_best['experiment_id']/'selection_scores.npy').astype(np.float32)
    for other in alternatives:
      secondary=np.load(OUT/'models'/other['experiment_id']/'selection_scores.npy').astype(np.float32)
      for fraction in (.25,.5,.75):
        assert_authorized(valid_ids,allowed)
        name=f'ensemble_{native_best["experiment_id"]}_{other["experiment_id"]}_{int(fraction*100)}'
        directory=OUT/'models'/name;directory.mkdir(exist_ok=True)
        if (directory/'result.json').exists():
            results.append(json.loads((directory/'result.json').read_text()));continue
        start=time.perf_counter();scores=np.float32(fraction)*primary+np.float32(1-fraction)*secondary
        m,f=metrics(valid,scores);components=[]
        for model,weight in ((native_best,fraction),(other,1-fraction)):
            artifact=OUT/'models'/model['experiment_id']/('model.txt' if model['model']=='LightGBM' else 'model.ubj')
            if sha(artifact)!=model['artifact_SHA']:raise PermissionError('Native ensemble artifact changed')
            components.append({'model':model['model'],'feature_set':model['feature_set'],'weight':weight,
              'artifact':artifact.relative_to(OUT).as_posix(),'artifact_SHA':model['artifact_SHA']})
        write_once(directory/'model.json',{'components':components,'aggregation':'ordered float32 weighted sum'})
        np.save(directory/'selection_scores.npy',scores)
        columns=[x for x in FEATURES if any(x in c['feature_set'] for c in components)]
        result={'experiment_id':name,'feature_set':columns,'model':'Ensemble','parameters':{'weights':[fraction,1-fraction]},
          'training_population':'native models: internal research train','selection_population':'internal model_select',
          'candidate_policy':'v1_plus_all','threshold_policy':'global >=0.61','candidate_count':len(scores),
          'metrics':m,'paired_vs_frozen':paired((f-baseline_f)[valid['active']]),'runtime':time.perf_counter()-start,
          'artifact_SHA':sha(directory/'model.json'),'status':'MODEL_SELECTION_ONLY',
          'runtime_scope':'cached score combination/evaluation; native training and inference costs are additional',
          'complexity':'two native model evaluations at inference'}
        write_once(directory/'result.json',result);ledger(result);results.append(result)
        print(name,m['macro_f05'],flush=True)
    chosen=select_simple_model(results)
    write_once(OUT/'selected_model_extension.json',{**chosen,
      'prior_selection_manifest_sha256':sha(OUT/'selected_model.json'),
      'extension_reason':'Wider model below 0.93. Test conditional context features and entity-balanced training; no policy/assessment outcomes used.',
      'selection_candidates':[r['experiment_id'] for r in results]})
    write_once(OUT/'selected_features_extension.json',{'chosen_model':chosen['experiment_id'],
      'features':chosen['feature_set'],'selection_role':1,'initial_freeze_preserved':'selected_features.json'})
resource=monitor.result();resource.update(duckdb_threads=2,duckdb_memory_mb=1000)
write_once(OUT/'extension_resources.json',resource)
print('extended selection',chosen['experiment_id'],chosen['metrics']['macro_f05'],flush=True)
