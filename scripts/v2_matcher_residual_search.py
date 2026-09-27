"""Bounded residual learning before one-time assessment; preserve initial policy."""
from pathlib import Path
import sys,json,time,gc
import numpy as np
import lightgbm as lgb
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.matcher_v2.data import OUT,FEATURES,write_once,ledger
from er.matcher_v2.experiments import role_data,validate_selected_artifacts
from er.matcher_v2.residual import context,logits,predict,EXTRA
from er.matcher_v2.policies import scalar_metric,selection_metrics,apply_policy
from er.matcher_v2.decision import entity_f05
from er.matcher_v2.access import sha,research_ids,assert_authorized
from er.candidates_v2.runtime import Monitor

if (OUT/'assessment.json').exists():raise PermissionError('No refinement after assessment')
if (OUT/'residual_search.json').exists():raise FileExistsError('Residual experiment already recorded')
base=validate_selected_artifacts();original=json.loads((OUT/'decision_policy.json').read_text())
with Monitor(OUT/'tmp') as monitor:
    train=role_data(1);valid=role_data(2)
    pt=np.load(OUT/'models'/base['experiment_id']/'selection_scores.npy')
    pv=np.load(OUT/'policy_raw_scores.npy')
    xt=context(train,pt);xv=context(valid,pv)
    keep=(train['y']==1)|(pt>=.05)|np.asarray(train['meta']['uniform_keep'],bool)
    weights=np.where((train['y'][keep]==0)&(pt[keep]<.05),16.,1.)
    train_x=np.column_stack((np.asarray(train['X'][keep]),xt[keep]))
    even=valid['entity']%2==0;active=valid['active'][valid['active']%2==0]
    valid_x=np.column_stack((np.asarray(valid['X'][even]),xv[even]))
    ent=valid['entity'][even];labels=valid['y'][even]
    def metric(prob,dataset):
        accepted=prob>=.7
        pn=np.bincount(ent[accepted],minlength=100000)
        tp=np.bincount(ent[accepted&(labels==1)],minlength=100000)
        return 'macro_f05',float(entity_f05(valid['truth'],pn,tp)[active].mean()),True
    results=[];odd=valid['active'][valid['active']%2==1]
    allowed=set(research_ids(ROOT))
    for depth,leaves in ((4,15),(6,31)):
        for d in (train,valid):assert_authorized(d['meta']['source1_entity_id'].to_pylist(),allowed)
        name=f'v2_residual_depth{depth}';directory=OUT/'models'/name;directory.mkdir(exist_ok=True)
        if (directory/'result.json').exists():results.append(json.loads((directory/'result.json').read_text()));continue
        start=time.perf_counter()
        settings={'objective':'binary','metric':'None','learning_rate':.035,'max_depth':depth,'num_leaves':leaves,
          'min_data_in_leaf':100,'lambda_l2':10.,'lambda_l1':1.,'feature_fraction':.9,'max_bin':63,
          'num_threads':2,'seed':42,'deterministic':True,'force_col_wise':True,'verbosity':-1}
        dt=lgb.Dataset(train_x,label=train['y'][keep],weight=weights,init_score=logits(pt[keep]),feature_name=FEATURES+EXTRA)
        dv=lgb.Dataset(valid_x,label=labels,init_score=logits(pv[even]),reference=dt,feature_name=FEATURES+EXTRA)
        print('residual fit',name,'rows',len(train_x),flush=True)
        model=lgb.train(settings,dt,num_boost_round=600,valid_sets=[dv],feval=metric,
          callbacks=[lgb.early_stopping(60,verbose=False),lgb.log_evaluation(50)])
        model.save_model(str(directory/'model.txt'));del dt,dv;gc.collect()
        corrected=predict(valid,pv,directory/'model.txt');np.save(directory/'policy_scores.npy',corrected)
        candidates=[]
        for threshold in np.arange(.2,.96,.025):
            candidates.append((scalar_metric(valid,corrected>=threshold,odd),float(round(threshold,4))))
        value,threshold=max(candidates)
        m,paired,count=selection_metrics(valid,corrected,corrected>=threshold)
        decision={'kind':'residual','artifact':(directory/'model.txt').relative_to(OUT).as_posix(),
          'artifact_SHA':sha(directory/'model.txt'),'threshold':threshold,'context_features':EXTRA}
        result={'experiment_id':name,'feature_set':FEATURES+EXTRA,'model':'LightGBM residual over frozen V2',
          'training_population':'model_select S1 only; base fit role 0','selection_population':'even policy early stopping; odd policy threshold selection',
          'candidate_policy':'v1_plus_all','threshold_policy':decision,'candidate_count':count,'metrics':m,
          'paired_vs_frozen':paired,'parameters':settings,'best_iteration':model.best_iteration,
          'runtime':time.perf_counter()-start,'artifact_SHA':decision['artifact_SHA'],'status':'POLICY_SELECTION_ONLY',
          'negative_sampling':'all positives and base score>=.05 negatives plus 1/16; sampled easy negatives weight16',
          'selection_optimism':'policy cohort was previously used to choose initial decision rules; assessment remains unopened'}
        write_once(directory/'result.json',result);ledger(result);results.append(result)
        print(name,value,threshold,flush=True)
    best=max(results,key=lambda r:r['metrics']['macro_f05'])
    improved=best['metrics']['macro_f05']-original['selection_macro_f05']>=.001
    if improved:
        policy={**original,'version':'matcher_sprint_r1_residual_refinement','calibration':'raw','calibration_parameters':{},
          'decision':best['threshold_policy'],'selection_macro_f05':best['metrics']['macro_f05'],
          'initial_policy_sha256':sha(OUT/'decision_policy.json'),
          'refinement_protocol':'role1 residual fitting; even policy early-stop; odd policy decision selection; assessment unopened'}
        write_once(OUT/'decision_policy_refined.json',policy)
    write_once(OUT/'residual_search.json',{'status':'RETAINED' if improved else 'REJECTED_BELOW_0p001',
      'initial_selection_macro_f05':original['selection_macro_f05'],'best':best,'trials':[r['experiment_id'] for r in results]})
resource=monitor.result();resource.update(duckdb_threads=2,duckdb_memory_mb=1000)
write_once(OUT/'residual_resources.json',resource)
print('residual verdict',improved,best['metrics']['macro_f05'],flush=True)
