"""Final bounded capacity check on development subsets; no assessment access."""
from pathlib import Path
import sys,json,time
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.matcher_v2.data import OUT,FEATURES,write_once,ledger
from er.matcher_v2.experiments import role_data,fit_one,select_simple_model,score_artifact,active_policy_path
from er.matcher_v2.policies import scalar_metric,selection_metrics,apply_policy
from er.matcher_v2.access import sha
from er.candidates_v2.runtime import Monitor

if (OUT/'assessment.json').exists():raise PermissionError('No capacity search after assessment')
if (OUT/'capacity_search.json').exists():raise FileExistsError('Capacity experiment recorded')
with Monitor(OUT/'tmp') as monitor:
    train=role_data(0);valid=role_data(1)
    original=json.loads((OUT/'selected_model_extension.json').read_text());results=[original]
    for leaves,depth,minimum in ((127,12,40),(255,14,30)):
        results.append(fit_one(f'v2_capacity_{leaves}',FEATURES,train,valid,
          {'num_leaves':leaves,'max_depth':depth,'min_data_in_leaf':minimum,'lambda_l2':5.,'lambda_l1':.5,
           'feature_fraction':.95,'learning_rate':.035},rounds=850))
    selected=select_simple_model(results)
    write_once(OUT/'selected_model_capacity.json',selected)
    del train,valid
    d=role_data(2);start=time.perf_counter()
    raw=score_artifact(d,selected)
    np.save(OUT/'capacity_policy_raw_scores.npy',raw)
    active=d['active'][d['active']%2==1];choices=[]
    for threshold in np.arange(.3,.91,.025):
        policy={'kind':'global','threshold':float(round(threshold,4))}
        choices.append((scalar_metric(d,raw>=threshold,active),policy))
    simple=max(choices,key=lambda r:r[0]);t=simple[1]['threshold']
    for a in (max(.05,t-.1),t,min(.95,t+.1)):
      for b in (max(.05,t-.1),t,min(.95,t+.1)):
        for kind in ('source','missing'):
            p={'kind':kind,**({'s2':a,'s3':b} if kind=='source' else {'missing':a,'present':b})}
            choices.append((scalar_metric(d,apply_policy(d,raw,p),active),p))
    p={'kind':'expected_set'};choices.append((scalar_metric(d,apply_policy(d,raw,p),active),p))
    best=max(choices,key=lambda r:r[0])
    if best[0]-simple[0]<.001:best=simple
    conflict={**best[1],'conflicts':True};v=scalar_metric(d,apply_policy(d,raw,conflict,conflict_active=active),active)
    choices.append((v,conflict))
    if v-best[0]>=.001:best=(v,conflict)
    accepted=apply_policy(d,raw,best[1],conflict_active=active)
    m,paired,count=selection_metrics(d,raw,accepted)
    previous_path=active_policy_path();previous=json.loads(previous_path.read_text())
    retained=best[0]-previous['selection_macro_f05']>=.001
    result={'experiment_id':'capacity_decision_selection','feature_set':selected['feature_set'],
      'model':selected['experiment_id'],'training_population':'research train role0','selection_population':'reused odd policy indices',
      'candidate_policy':'v1_plus_all','threshold_policy':best[1],'candidate_count':count,'metrics':m,
      'paired_vs_frozen':paired,'runtime':time.perf_counter()-start,'artifact_SHA':selected['artifact_SHA'],
      'status':'POLICY_SELECTION_ONLY','retained':retained,'previous_macro_f05':previous['selection_macro_f05']}
    ledger(result)
    if retained:
        write_once(OUT/'decision_policy_capacity.json',{'version':'matcher_sprint_r1_capacity',
          'calibration':'raw','calibration_parameters':{},'decision':best[1],
          'model_manifest_file':'selected_model_capacity.json','model_manifest_sha256':sha(OUT/'selected_model_capacity.json'),
          'feature_spec_sha256':sha(OUT/'feature_spec.json'),'selection_macro_f05':best[0],
          'frozen_before_assessment':True,'previous_policy_sha256':sha(previous_path),
          'selection_optimism':'Development subsets reused adaptively; no independent claim until assessment'})
    write_once(OUT/'capacity_search.json',{'result':result,'policies':[{'macro_f05':v,'policy':p} for v,p in choices],
       'models':[r['experiment_id'] for r in results]})
resource=monitor.result();resource.update(duckdb_threads=2,duckdb_memory_mb=1000)
write_once(OUT/'capacity_resources.json',resource)
print('capacity policy',best,'retained',retained,flush=True)
