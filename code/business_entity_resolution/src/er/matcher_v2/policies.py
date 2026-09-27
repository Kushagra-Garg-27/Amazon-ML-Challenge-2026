"""Calibrated and entity-adaptive decisions, selected before sprint assessment."""
import json
import time
import numpy as np
import pyarrow.parquet as pq
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression
from .data import OUT,FEATURES,write_once,ledger,connection
from .access import sha
from .experiments import role_data,score_selected,metrics,selected_manifest_path,validate_selected_artifacts,active_policy_path
from .evaluate import paired
from .decision import choose_expected_set,resolve_conflicts,entity_f05
from .entity_utility import summary_features,forest_artifact,predict_forest

def logit(p):return np.log(np.clip(p,1e-6,1-1e-6)/np.clip(1-p,1e-6,1))

def apply_calibration(scores,kind,artifact):
    if kind=='raw':return scores
    if kind=='platt':
        z=np.clip(artifact['coef']*logit(scores)+artifact['intercept'],-40,40)
        return (1/(1+np.exp(-z))).astype(np.float32)
    return np.interp(scores,artifact['x'],artifact['y']).astype(np.float32)

def curve(p,y):
    index=np.minimum((p*10).astype(int),9)
    return [{'bin':i,'rows':int((index==i).sum()),'predicted':float(p[index==i].mean()) if (index==i).any() else None,
      'observed':float(y[index==i].mean()) if (index==i).any() else None} for i in range(10)]

def boundaries(entity):
    return np.r_[0,np.flatnonzero(entity[1:]!=entity[:-1])+1,len(entity)]

def apply_policy(d,scores,policy,conflict_active=None,return_scores=False):
    kind=policy['kind'];p=scores
    if kind=='global':accepted=p>=policy['threshold']
    elif kind=='residual':
        artifact_path=OUT/policy['artifact']
        if sha(artifact_path)!=policy['artifact_SHA']:raise PermissionError('Residual model changed')
        from .residual import predict
        p=predict(d,p,artifact_path)
        accepted=p>=policy['threshold']
    elif kind=='utility':
        artifact_path=OUT/policy['artifact']
        if sha(artifact_path)!=policy['artifact_SHA']:raise PermissionError('Entity utility model changed')
        artifact=json.loads(artifact_path.read_text())
        x=summary_features(d,p);utilities=predict_forest(artifact,x)
        thresholds=np.asarray(artifact['threshold_choices'])[np.argmax(utilities,axis=1)]
        accepted=p>=thresholds[d['entity']]
    elif kind=='source':accepted=p>=np.where(d['source'],policy['s2'],policy['s3'])
    elif kind=='missing':accepted=p>=np.where(d['address'],policy['missing'],policy['present'])
    elif kind=='context':
        count=np.asarray(d['X'][:,FEATURES.index('candidate_count')]);cross=np.asarray(d['X'][:,FEATURES.index('cross_joint')])
        threshold=policy['threshold']+policy['dense_penalty']*(count>250)-policy['support_discount']*(cross>=.8)
        accepted=p>=np.clip(threshold,.05,.95)
    elif kind=='expected_set':
        accepted=np.zeros(len(p),bool)
        for left,right in zip(boundaries(d['entity'])[:-1],boundaries(d['entity'])[1:]):
            accepted[left:right]=choose_expected_set(p[left:right])
    elif kind=='gap':
        accepted=p>=policy['threshold']
        for left,right in zip(boundaries(d['entity'])[:-1],boundaries(d['entity'])[1:]):
            q=p[left:right];order=np.argsort(-q,kind='stable')
            if len(q)>1 and q[order[0]]>=policy['top_min'] and q[order[0]]-q[order[1]]>=policy['gap']:
                accepted[left:right]=False;accepted[left+order[0]]=True
    else:raise ValueError(kind)
    if policy.get('conflicts'):
        target=d['meta']['target_entity_id'].to_numpy()
        if conflict_active is not None:accepted=accepted&np.isin(d['entity'],conflict_active)
        accepted=resolve_conflicts(d['entity'],target,p,accepted)
    return (accepted,p) if return_scores else accepted

def scalar_metric(d,accepted,selection_active):
    pn=np.bincount(d['entity'][accepted],minlength=100000)
    tp=np.bincount(d['entity'][accepted&(d['y']==1)],minlength=100000)
    return float(entity_f05(d['truth'],pn,tp)[selection_active].mean())

def utility_targets(d,scores,thresholds):
    targets=[]
    for threshold in thresholds:
        accepted=scores>=threshold
        pn=np.bincount(d['entity'][accepted],minlength=100000)
        tp=np.bincount(d['entity'][accepted&(d['y']==1)],minlength=100000)
        targets.append(entity_f05(d['truth'],pn,tp))
    return np.column_stack(targets)

def selection_metrics(d,scores,accepted):
    """Complete candidate and truth metrics on odd policy entities only."""
    mask=d['entity']%2==1;subset={**d,'active':d['active'][d['active']%2==1]}
    for key in ('entity','y','source','address','script','baseline'):subset[key]=d[key][mask]
    den={};c=connection('policy_metric')
    for name,expr in [('source',"d.target_source='S2'"),('address','NOT d.both_address'),
      ('script',"split_part(d.script_relation,':',1)<>split_part(d.script_relation,':',2) AND NOT contains(d.script_relation,'0')")]:
        den[name]={'False':0,'True':0}
        query=f"""SELECT ({expr}) AS slice_value,count(*)
          FROM read_parquet('work/v2_research/v1_link_diagnostics.parquet') d
          JOIN read_parquet('{(OUT/'allocation.parquet').as_posix()}') a USING(s1)
          WHERE a.role=2 AND a.entity_index%2=1 GROUP BY 1"""
        for value,n in c.sql(query).fetchall():den[name][str(value)]=n
    c.close();subset['den']=den
    m,f=metrics(subset,scores[mask],accepted=accepted[mask]);_,bf=metrics(subset,subset['baseline'])
    return m,paired((f-bf)[subset['active']]),int(mask.sum())

def tune():
    start=time.perf_counter();d=role_data(2);raw=score_selected(d)
    # Model-selection predictions are out of pair-model training. They may fit
    # an additional meta-learner; odd policy entities remain its validation.
    meta=role_data(1);meta_raw=score_selected(meta)
    np.save(OUT/'policy_raw_scores.npy',raw)
    calibration_rows=d['entity']%2==0
    selection_active=d['active'][d['active']%2==1]
    platt=LogisticRegression(C=1000,solver='lbfgs',max_iter=200).fit(logit(raw[calibration_rows]).reshape(-1,1),d['y'][calibration_rows])
    iso=IsotonicRegression(out_of_bounds='clip').fit(raw[calibration_rows],d['y'][calibration_rows])
    calibration={'raw':{},'platt':{'coef':float(platt.coef_[0,0]),'intercept':float(platt.intercept_[0])},
      'isotonic':{'x':iso.X_thresholds_.tolist(),'y':iso.y_thresholds_.tolist()}}
    write_once(OUT/'calibration.json',{'fit':'policy_select even entity_index','selection':'policy_select odd entity_index',
      'methods':calibration,'future_assessment_used':False})
    choices=[];calmetrics={}
    for method,artifact in calibration.items():
        scores=apply_calibration(raw,method,artifact);held=~calibration_rows
        calmetrics[method]={'brier':float(np.mean((scores[held]-d['y'][held])**2)),
          'curve':curve(scores[held],d['y'][held])}
        global_choices=[]
        for threshold in np.arange(.10,.91,.05):
            policy={'kind':'global','threshold':float(round(threshold,3))}
            value=scalar_metric(d,apply_policy(d,scores,policy),selection_active)
            global_choices.append((value,policy));choices.append((value,method,policy))
        _,best_global=max(global_choices,key=lambda a:a[0]);t=best_global['threshold']
        for a in (max(.05,t-.1),t,min(.95,t+.1)):
            for b in (max(.05,t-.1),t,min(.95,t+.1)):
                for kind in ('source','missing'):
                    policy={'kind':kind,**({'s2':a,'s3':b} if kind=='source' else {'missing':a,'present':b})}
                    choices.append((scalar_metric(d,apply_policy(d,scores,policy),selection_active),method,policy))
        for dense in (0.,.05):
            for discount in (0.,.05,.10):
                policy={'kind':'context','threshold':t,'dense_penalty':dense,'support_discount':discount}
                choices.append((scalar_metric(d,apply_policy(d,scores,policy),selection_active),method,policy))
        for gap in (.2,.4):
            policy={'kind':'gap','threshold':t,'top_min':.8,'gap':gap}
            choices.append((scalar_metric(d,apply_policy(d,scores,policy),selection_active),method,policy))
        policy={'kind':'expected_set'}
        choices.append((scalar_metric(d,apply_policy(d,scores,policy),selection_active),method,policy))
        # Each tree predicts the actual macro-metric contribution of each set
        # choice. Its training labels are confined to the calibration-fit half.
        from sklearn.ensemble import RandomForestRegressor
        x=summary_features(d,scores)
        thresholds=[.15,.25,.35,.45,.55,.61,.65,.70,.75,.80,.85,.90,.95,.98,1.01]
        utility=utility_targets(d,scores,thresholds);fit_active=d['active'][d['active']%2==0]
        meta_scores=apply_calibration(meta_raw,method,calibration[method])
        meta_x=summary_features(meta,meta_scores);meta_y=utility_targets(meta,meta_scores,thresholds)
        for scope in ('calibration_only','model_selection_plus_calibration'):
          fit_x=x[fit_active];fit_y=utility[fit_active]
          if scope=='model_selection_plus_calibration':
            fit_x=np.concatenate((fit_x,meta_x[meta['active']]))
            fit_y=np.concatenate((fit_y,meta_y[meta['active']]))
          for depth in (4,6):
            model=RandomForestRegressor(n_estimators=64,max_depth=depth,min_samples_leaf=60,
                max_features=.8,n_jobs=2,random_state=42)
            model.fit(fit_x,fit_y)
            artifact=forest_artifact(model);artifact['threshold_choices']=thresholds
            artifact['fit_scope']=scope+'; no odd policy or assessment outcomes; macro F0.5 utility targets'
            artifact['fit_s1']=len(fit_x)
            path=OUT/'entity_utility'/f'{method}_{scope}_depth{depth}.json';write_once(path,artifact)
            policy={'kind':'utility','artifact':path.relative_to(OUT).as_posix(),'artifact_SHA':sha(path)}
            predicted=predict_forest(artifact,x)
            row_threshold=np.asarray(thresholds)[np.argmax(predicted,axis=1)][d['entity']]
            choices.append((scalar_metric(d,scores>=row_threshold,selection_active),method,policy))
    choices.sort(key=lambda r:-r[0])
    best_value,method,best=choices[0]
    # Complexity rule: prefer the best scalar threshold when context adds <0.001.
    simple=max((x for x in choices if x[2]['kind']=='global'),key=lambda x:x[0])
    rejected_complex=False
    if best_value-simple[0]<.001 and best['kind']!='global':
        best_value,method,best=simple;rejected_complex=True
    p=apply_calibration(raw,method,calibration[method]);with_conflict={**best,'conflicts':True}
    conflict_metric=scalar_metric(d,apply_policy(d,p,with_conflict,conflict_active=selection_active),selection_active)
    if conflict_metric-best_value>=.001:best_value=conflict_metric;best=with_conflict
    accepted=apply_policy(d,p,best);m,f=metrics(d,p,accepted=accepted);_,bf=metrics(d,d['baseline'])
    selection_accepted=apply_policy(d,p,best,conflict_active=selection_active)
    selected_metrics,selected_paired,selected_count=selection_metrics(d,p,selection_accepted)
    if abs(selected_metrics['macro_f05']-best_value)>1e-12:raise RuntimeError('Selection metric disagreement')
    policy={'version':'matcher_sprint_r1','calibration':method,'calibration_parameters':calibration[method],
      'decision':best,'model_manifest_file':selected_manifest_path().name,
      'model_manifest_sha256':sha(selected_manifest_path()),'feature_spec_sha256':sha(OUT/'feature_spec.json'),
      'selection_macro_f05':best_value,'complex_policy_rejected_below_0p001':rejected_complex,
      'conflict_variant_macro_f05':conflict_metric,'frozen_before_assessment':True}
    write_once(OUT/'decision_policy.json',policy)
    write_once(OUT/'policy_search.json',{'candidates':[{'macro_f05':v,'calibration':k,'policy':p} for v,k,p in choices],
      'calibration_metrics':calmetrics,'selected':policy,'whole_policy_role_metrics':m,
      'selection_metrics':selected_metrics,'paired_vs_frozen':selected_paired,
      'whole_policy_role_metrics_scope':'includes calibration fit; descriptive only','runtime':time.perf_counter()-start})
    ledger({'experiment_id':'decision_selection','feature_set':'selected model','model':'frozen selected V2 model',
      'training_population':'pair model: internal train; calibration: even policy indices; utility: even policy and optional model_select',
      'selection_population':'odd policy indices only',
      'candidate_policy':'v1_plus_all','threshold_policy':policy,
      'candidate_count':selected_count,'metrics':selected_metrics,'runtime':time.perf_counter()-start,'artifact_SHA':sha(OUT/'decision_policy.json'),
      'status':'POLICY_SELECTION_ONLY'})
    print('policy selected',method,best,'selection F05',best_value,'whole policy',m['macro_f05'],flush=True)

def assessment():
    if (OUT/'assessment.json').exists():raise RuntimeError('One-time assessment already complete; use reproduce stage')
    policy=json.loads(active_policy_path().read_text());policy_sha=sha(active_policy_path())
    selected=validate_selected_artifacts(require_policy=True)
    d=role_data(3);start=time.perf_counter();raw=score_selected(d)
    p=apply_calibration(raw,policy['calibration'],policy['calibration_parameters'])
    accepted,decision_scores=apply_policy(d,p,policy['decision'],return_scores=True)
    m,f=metrics(d,decision_scores,accepted=accepted);base,bf=metrics(d,d['baseline'])
    np.save(OUT/'assessment_raw_scores.npy',raw)
    pq.write_table(pa_table(d,raw,p,accepted,decision_scores),OUT/'assessment_predictions.parquet',compression='zstd')
    result={'status':'COMPLETE_ONE_TIME_INTERNAL_ASSESSMENT','role':'research assessment only',
      'metrics':m,'frozen_v1_baseline':base,'paired_vs_frozen':paired((f-bf)[d['active']]),
      'policy_file':active_policy_path().name,'policy_sha256_before_labels':policy_sha,'model_artifact_sha256':selected['artifact_SHA'],
      'prediction_sha256':sha(OUT/'assessment_predictions.parquet'),'runtime':time.perf_counter()-start,
      'claim_scope':'internal S1 split of previously exposed research population; not an untouched holdout and not the full-research score',
      'next_stage':'stop; no model/policy changes from this assessment'}
    write_once(OUT/'assessment.json',result)
    ledger({'experiment_id':'final_internal_assessment','feature_set':selected['feature_set'],'model':selected['model'],
      'training_population':'research internal train','candidate_policy':'v1_plus_all','threshold_policy':policy['decision'],
      'candidate_count':len(raw),'metrics':m,'runtime':result['runtime'],'artifact_SHA':result['prediction_sha256'],
      'status':result['status']})
    print(json.dumps(result,indent=2),flush=True)

def pa_table(d,raw,p,accepted,decision_scores):
    import pyarrow as pa
    return pa.table({'source1_entity_id':d['meta']['source1_entity_id'],
      'target_entity_id':d['meta']['target_entity_id'],'raw_score':raw,'calibrated_score':p,
      'decision_score':decision_scores,'accepted':accepted})

def reproduce():
    expected=json.loads((OUT/'assessment.json').read_text());policy=json.loads(active_policy_path().read_text())
    if sha(active_policy_path())!=expected['policy_sha256_before_labels']:raise RuntimeError('Policy changed')
    if sha(OUT/'assessment_predictions.parquet')!=expected['prediction_sha256']:raise RuntimeError('Saved assessment artifact changed')
    d=role_data(3);raw=score_selected(d);p=apply_calibration(raw,policy['calibration'],policy['calibration_parameters'])
    accepted,decision_scores=apply_policy(d,p,policy['decision'],return_scores=True);previous=np.load(OUT/'assessment_raw_scores.npy')
    if not np.array_equal(previous,raw):raise RuntimeError('Prediction reproduction differs')
    prior=__import__('pyarrow.parquet',fromlist=['read_table']).read_table(OUT/'assessment_predictions.parquet')
    if not np.array_equal(np.asarray(prior['decision_score']),decision_scores):raise RuntimeError('Decision score reproduction differs')
    if not np.array_equal(np.asarray(prior['accepted']),accepted):raise RuntimeError('Decision reproduction differs')
    m,_=metrics(d,p,accepted=accepted)
    if abs(m['macro_f05']-expected['metrics']['macro_f05'])>1e-12:raise RuntimeError('Metric reproduction differs')
    if not (OUT/'reproduction.json').exists():
        write_once(OUT/'reproduction.json',{'status':'PASS','exact_score_array_equal':True,'exact_decisions_equal':True,
          'exact_decision_scores_equal':True,'macro_f05':m['macro_f05'],'command':'.venv\\Scripts\\python.exe -B scripts\\v2_matcher_sprint.py reproduce'})
    print('REPRODUCED',m['macro_f05'])

def run(stage):
    if stage=='decision':tune()
    elif stage=='assessment':assessment()
    else:reproduce()
