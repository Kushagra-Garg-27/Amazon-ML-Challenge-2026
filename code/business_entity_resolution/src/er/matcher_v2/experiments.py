"""Nested research roles: training → model selection → policy selection → assessment."""
from pathlib import Path
import gc
import json
import os
import time
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import lightgbm as lgb
from .access import assert_authorized,research_ids,ROLE_NAMES,sha
from .data import ROOT,OUT,V1,FEATURES,GROUPS,connection,allocation,write_once,ledger
from .evaluate import evaluate,paired
from .decision import entity_f05,choose_expected_set,resolve_conflicts

def features_guard():
    a=allocation();manifest=json.loads((OUT/'features/manifest.json').read_text())
    if sha(ROOT/'work/final_matcher_model.txt')!='76ff78a7cc97e40174b6d631a6eaa062c786f0480c6693fd6387e90be6761d21':
        raise PermissionError('Frozen V1 model changed')
    if sha(ROOT/'work/v2_research/research_gt.parquet')!=json.loads((ROOT/'work/v2_research/research_gt_receipt.json').read_text())['sha256']:
        raise PermissionError('Research labels changed')
    if manifest['status']!='COMPLETE':raise RuntimeError('Incomplete features')
    if sha(OUT/'feature_spec.json')!=manifest['feature_spec_sha256']:raise PermissionError('Materialized feature specification changed')
    if json.loads((OUT/'feature_spec.json').read_text())['feature_order']!=FEATURES:raise PermissionError('Runtime feature order differs')
    for p in manifest['parts']:
        if sha(ROOT/p['path'])!=p['sha256']:raise RuntimeError('Feature artifact changed')
    return a

def active_policy_path(output=None):
    output=OUT if output is None else Path(output)
    capacity=output/'decision_policy_capacity.json'
    if capacity.exists():return capacity
    refined=output/'decision_policy_refined.json'
    return refined if refined.exists() else output/'decision_policy.json'


def selected_manifest_path(output=None,require_policy=False):
    output=OUT if output is None else Path(output)
    if require_policy:
        policy=json.loads(active_policy_path(output).read_text())
        return output/policy.get('model_manifest_file','selected_model.json')
    extension=output/'selected_model_extension.json'
    return extension if extension.exists() else output/'selected_model.json'


def validate_selected_artifacts(output=None,root=None,require_policy=False):
    output=OUT if output is None else Path(output)
    root=ROOT if root is None else Path(root)
    manifest_path=selected_manifest_path(output,require_policy)
    selected=json.loads(manifest_path.read_text())
    if selected['model']=='Frozen V1':artifact=root/'work/final_matcher_model.txt'
    else:artifact=output/'models'/selected['experiment_id']/({'LightGBM':'model.txt','XGBoost':'model.ubj','Ensemble':'model.json'}[selected['model']])
    if sha(artifact)!=selected['artifact_SHA']:raise PermissionError('Selected model bytes changed')
    if selected['model']=='Ensemble':
        for component in json.loads(artifact.read_text())['components']:
            if sha(output/component['artifact'])!=component['artifact_SHA']:raise PermissionError('Ensemble component changed')
    if require_policy:
        policy=json.loads(active_policy_path(output).read_text())
        if sha(manifest_path)!=policy['model_manifest_sha256']:raise PermissionError('Frozen model manifest changed')
        if sha(output/'feature_spec.json')!=policy['feature_spec_sha256']:raise PermissionError('Frozen feature specification changed')
        decision=policy['decision']
        if decision['kind']=='utility' and sha(output/decision['artifact'])!=decision['artifact_SHA']:
            raise PermissionError('Frozen entity utility artifact changed')
        if decision['kind']=='residual' and sha(output/decision['artifact'])!=decision['artifact_SHA']:
            raise PermissionError('Frozen residual model artifact changed')
    return selected


def select_simple_model(results,minimum_gain=.001):
    best=max(results,key=lambda r:r['metrics']['macro_f05'])
    viable=[r for r in results if best['metrics']['macro_f05']-r['metrics']['macro_f05']<minimum_gain]
    return min(viable,key=lambda r:(r.get('model')=='Ensemble',len(r['feature_set']),r.get('parameters',{}).get('num_leaves',31),r.get('runtime',0),-r['metrics']['macro_f05']))


def role_data(role):
    if role not in range(4):raise PermissionError('Unknown experiment role')
    if role==2 and not (OUT/'selected_model.json').exists():raise PermissionError('Model selection must finish before policy labels')
    if role==3 and not (OUT/'decision_policy.json').exists():raise PermissionError('Policy must be frozen before assessment labels')
    if role>=2:validate_selected_artifacts(require_policy=role==3)
    a=features_guard();allowed=set(research_ids(ROOT));c=connection('matrices')
    c.execute(f"CREATE VIEW alloc AS SELECT * FROM read_parquet('{(OUT/'allocation.parquet').as_posix()}')")
    path=OUT/'matrices'/ROLE_NAMES[role];path.mkdir(parents=True,exist_ok=True)
    meta_path=path/'metadata.parquet';x_path=path/'X.npy';receipt_path=path/'manifest.json'
    if not receipt_path.exists():
        c.execute(f"CREATE TEMP TABLE selected_gt AS SELECT g.* FROM read_parquet('work/v2_research/research_gt.parquet') g JOIN alloc a USING(s1) WHERE a.role={role}")
        query=f"""SELECT f.* EXCLUDE(y_train), (g.mid IS NOT NULL)::UTINYINT AS "label"
          FROM read_parquet('{(OUT/'features/*.parquet').as_posix()}') f
          LEFT JOIN selected_gt g ON f.source1_entity_id=g.s1 AND f.target_entity_id=g.mid
          WHERE f.role={role} ORDER BY f.entity_index,f.target_entity_id"""
        count=c.sql(f"SELECT count(*) FROM read_parquet('{(OUT/'features/*.parquet').as_posix()}') WHERE role={role}").fetchone()[0]
        x=np.lib.format.open_memmap(x_path,mode='w+',dtype=np.float32,shape=(count,len(FEATURES)))
        writer=None;offset=0
        for batch in c.execute(query).fetch_record_batch(50000):
            b=pa.Table.from_batches([batch]);assert_authorized(b['source1_entity_id'].to_pylist(),allowed)
            values=np.column_stack([np.asarray(b[col],dtype=np.float32) for col in FEATURES])
            x[offset:offset+len(b)]=values
            meta=b.select(['source1_entity_id','target_entity_id','entity_index','role','uniform_keep','label',
              'target_is_s2','target_address_missing','s1_address_missing','script_conflict','v1_score'])
            if writer is None:writer=pq.ParquetWriter(meta_path,meta.schema,compression='zstd')
            writer.write_table(meta);offset+=len(b)
        writer.close();x.flush();del x
        if offset!=count:raise RuntimeError('Matrix row mismatch')
        write_once(receipt_path,{'role':ROLE_NAMES[role],'rows':count,'columns':FEATURES,
          'matrix_sha256':sha(x_path),'metadata_sha256':sha(meta_path),'allocation_sha256':sha(OUT/'allocation.parquet')})
    receipt=json.loads(receipt_path.read_text())
    if receipt['columns']!=FEATURES or receipt['allocation_sha256']!=sha(OUT/'allocation.parquet'):
        raise PermissionError('Matrix feature order or allocation differs')
    if sha(meta_path)!=receipt['metadata_sha256'] or sha(x_path)!=receipt['matrix_sha256']:
        raise RuntimeError('Matrix/metadata changed')
    metadata=pq.read_table(meta_path);assert_authorized(metadata['source1_entity_id'].to_pylist(),allowed)
    entity=np.asarray(metadata['entity_index'],dtype=np.int32);y=np.asarray(metadata['label'],dtype=np.uint8)
    active=np.asarray(a['entity_index'])[np.asarray(a['role'])==role]
    truth=np.zeros(100000,np.int32)
    rows=c.sql(f"SELECT a.entity_index,t.truth_n FROM read_parquet('work/v2_research/research_truth_counts.parquet') t JOIN alloc a USING(s1) WHERE role={role}").fetchall()
    for i,n in rows:truth[i]=n
    den={}
    # Truth-link metadata is restricted to this experiment role before decoding.
    for name,expr in [('source',"d.target_source='S2'"),('address','NOT d.both_address'),('script',"split_part(d.script_relation,':',1)<>split_part(d.script_relation,':',2) AND NOT contains(d.script_relation,'0')")]:
        den[name]={str(False):0,str(True):0}
        for value,n in c.sql(f"SELECT ({expr}) AS slice_value,count(*) FROM read_parquet('work/v2_research/v1_link_diagnostics.parquet') d JOIN alloc a USING(s1) WHERE a.role={role} GROUP BY 1").fetchall():den[name][str(value)]=n
    c.close()
    return {'X':np.load(x_path,mmap_mode='r'),'meta':metadata,'entity':entity,'y':y,'truth':truth,'active':active,'den':den,
      'source':np.asarray(metadata['target_is_s2'],bool),'address':np.asarray(metadata['target_address_missing'],bool)|np.asarray(metadata['s1_address_missing'],bool),
      'script':np.asarray(metadata['script_conflict'],bool),'baseline':np.asarray(metadata['v1_score'],np.float32),'role':role}

def metrics(d,scores,threshold=.61,accepted=None):
    return evaluate(d['entity'],d['y'],scores,d['truth'],d['active'],threshold,accepted,
      d['source'],d['address'],d['script'],d['den'])

def diagnostic_errors():
    d=role_data(0);x=d['X'];y=d['y'];score=d['baseline'];cols={n:i for i,n in enumerate(FEATURES)}
    accepted=score>=.61;n=100000
    pred=np.bincount(d['entity'][accepted],minlength=n)
    masks={'A_strong_evidence_FN':(y==1)&~accepted&((x[:,cols['name_ratio']]>=.8)|(x[:,cols['address_ratio']]>=.8)),
      'B_similar_FP':(y==0)&accepted&((x[:,cols['name_ratio']]>=.8)|(x[:,cols['address_ratio']]>=.8)),
      'B_all_FP':(y==0)&accepted,'C_TP':(y==1)&accepted,
      'D_hard_TN':(y==0)&~accepted&(score>=.05),
      'E_underpredicted':pred[d['entity']]<d['truth'][d['entity']],
      'F_overpredicted':pred[d['entity']]>d['truth'][d['entity']],
      'G_address_missing':d['address'],'H_script_conflict':d['script']}
    s2=np.bincount(d['entity'][(y==1)&d['source']],minlength=n)
    s3=np.bincount(d['entity'][(y==1)&~d['source']],minlength=n)
    for label,mask in [('I_S2_only',(s2>0)&(s3==0)),('J_S3_only',(s3>0)&(s2==0)),('K_multisource',(s2>0)&(s3>0))]:masks[label]=mask[d['entity']]
    signals=['name_ratio','name_token_sort_ratio','name_ng2','name_ng3','name_ng4','name_wratio',
      'name_levenshtein','name_jaro','name_weighted_jaccard','initial_compat','address_ratio','address_jaccard',
      'addr_ng3','addr_locality_jaccard','number_jaccard','postal_equal_v2','house_number_equal',
      'name_token_intersection','name_shared_idf','addr_weighted_jaccard','script_conflict','translit_ratio',
      'source_balanced_rank','target_is_s2','candidate_count','addr_missing','s1_name_chars','target_name_chars',
      's1_address_chars','target_address_chars','cross_name','cross_joint','cross_support_count']
    result={}
    for label,mask in masks.items():
        result[label]={'pairs':int(mask.sum()),'s1':int(np.unique(d['entity'][mask]).size),'distributions':{}}
        for feature in signals:
            v=np.asarray(x[:,cols[feature]])[mask]
            result[label]['distributions'][feature]={'mean':float(v.mean()) if len(v) else None,
              'p10_p50_p90':np.quantile(v,[.1,.5,.9]).tolist() if len(v) else []}
    separation={}
    positive=y==1;negative=(y==0)&(score>=.05)
    for feature in sum(GROUPS.values(),[]):
        p=np.asarray(x[:,cols[feature]])[positive];q=np.asarray(x[:,cols[feature]])[negative]
        effect=abs(float(p.mean()-q.mean()))/max(float(np.sqrt((p.var()+q.var())/2)),1e-8)
        separation[feature]={'standardized_mean_separation':effect,'positive_mean':float(p.mean()),'hard_negative_mean':float(q.mean())}
    # Outcome-guided feature reduction is confined to the internal training role.
    chosen=[]
    for feature,z in sorted(separation.items(),key=lambda a:-a[1]['standardized_mean_separation']):
        if z['standardized_mean_separation']<.10:continue
        v=np.asarray(x[::7,cols[feature]])
        if any(abs(np.corrcoef(v,np.asarray(x[::7,cols[other]]))[0,1])>.995 for other in chosen):continue
        chosen.append(feature)
    c=connection('error_audit')
    target_conflicts=c.sql(f"""WITH g AS (SELECT g.mid,count(DISTINCT g.s1) n
      FROM read_parquet('work/v2_research/research_gt.parquet') g
      JOIN read_parquet('{(OUT/'allocation.parquet').as_posix()}') a USING(s1)
      WHERE a.role=0 GROUP BY 1) SELECT count(*) FILTER(WHERE n>1),max(n) FROM g""").fetchone()
    c.close()
    write_once(OUT/'error_analysis.json',{'role':'train only','classes':result,'separation':separation,
      'selected_nonredundant':chosen,'sampling_scope':'all retrieved positives, score>=.05 negatives and 1/16 deterministic random negatives',
      'training_truth_target_assignment_diagnostic':{'targets_linked_to_multiple_s1':target_conflicts[0],'max_s1_per_target':target_conflicts[1],
        'used_for_feature_construction':False,'does_not_establish_test_constraint':True},
      'source_entity_classes':'based on candidate-recovered training truth only; diagnostics are not features'})
    print('error classes', {k:v['pairs'] for k,v in result.items()},'nonredundant',chosen,flush=True)

def predict_lgb(model,x,columns):
    idx=[FEATURES.index(x) for x in columns];out=np.empty(len(x),np.float32)
    for start in range(0,len(x),100000):
        out[start:start+100000]=model.predict(np.asarray(x[start:start+100000,idx],dtype=np.float32),num_threads=2)
    return out

def entity_training_weights(entity,y,truth):
    positive=y==1;negative_count=np.bincount(entity[~positive],minlength=len(truth))
    weight=np.where(positive,1/np.maximum(truth[entity],1),1/np.maximum(negative_count[entity],1)).astype(np.float32)
    for mask in (positive,~positive):
        if mask.any():weight[mask]/=weight[mask].mean()
    return weight


def fit_one(name,columns,train,valid,params=None,uniform=False,rounds=500,entity_balanced=False):
    allowed=set(research_ids(ROOT))
    for d in (train,valid):assert_authorized(d['meta']['source1_entity_id'].to_pylist(),allowed)
    directory=OUT/'models'/name;directory.mkdir(parents=True,exist_ok=True)
    if (directory/'result.json').exists():return json.loads((directory/'result.json').read_text())
    started=time.perf_counter();idx=[FEATURES.index(x) for x in columns]
    keep=(train['y']==1)|np.asarray(train['meta']['uniform_keep'],bool) if uniform else np.ones(len(train['y']),bool)
    xt=np.asarray(train['X'][:,idx][keep],dtype=np.float32)
    # Evaluation dataset is the full selection candidate set, not a negative sample.
    xv=np.asarray(valid['X'][:,idx],dtype=np.float32)
    settings={'objective':'binary','metric':'None','learning_rate':.05,'num_leaves':31,'max_depth':8,
      'min_data_in_leaf':100,'feature_fraction':.9,'bagging_fraction':.9,'bagging_freq':1,'lambda_l2':1.,
      'num_threads':2,'seed':42,'deterministic':True,'force_col_wise':True,'verbosity':-1,'max_bin':63}
    if params:settings.update(params)
    weight=entity_training_weights(train['entity'][keep],train['y'][keep],train['truth']) if entity_balanced else None
    dt=lgb.Dataset(xt,label=train['y'][keep],weight=weight,feature_name=columns,free_raw_data=True)
    dv=lgb.Dataset(xv,label=valid['y'],reference=dt,feature_name=columns,free_raw_data=True)
    truth=valid['truth'];entity=valid['entity'];active=valid['active'];positive=valid['y']==1
    def metric(pred,dataset):
        accepted=pred>=.61
        pn=np.bincount(entity[accepted],minlength=100000)
        tp=np.bincount(entity[accepted&positive],minlength=100000)
        return 'macro_f05',float(entity_f05(truth,pn,tp)[active].mean()),True
    print('fit',name,'features',len(columns),'train',len(xt),'valid',len(xv),flush=True)
    model=lgb.train(settings,dt,num_boost_round=rounds,valid_sets=[dv],feval=metric,
      callbacks=[lgb.early_stopping(45,verbose=False),lgb.log_evaluation(50)])
    model.save_model(str(directory/'model.txt'))
    del xt,xv,dt,dv;gc.collect()
    scores=predict_lgb(model,valid['X'],columns);np.save(directory/'selection_scores.npy',scores)
    m,f=metrics(valid,scores);base,bf=metrics(valid,valid['baseline'])
    result={'experiment_id':name,'feature_set':columns,'model':'LightGBM','parameters':settings,
      'best_iteration':model.best_iteration,'training_population':'research internal train 59856 S1',
      'selection_population':'research internal model_select 14969 S1; full candidates',
      'candidate_policy':'v1_plus_all frozen','threshold_policy':'global >=0.61 for model comparison',
      'candidate_count':len(scores),'metrics':m,'paired_vs_frozen':paired((f-bf)[valid['active']]),
      'runtime':time.perf_counter()-started,'artifact_SHA':sha(directory/'model.txt'),
      'status':'MODEL_SELECTION_ONLY','positive_training_pairs':int(train['y'][keep].sum()),
      'training_rows':int(keep.sum()),'negative_policy':'uniform 1/16' if uniform else 'uniform 1/16 plus frozen score>=0.05'}
    result['training_weight_policy']='inverse complete truth count for positives, inverse sampled negative count for negatives; each class renormalized to mean one' if entity_balanced else 'uniform pair weights'
    write_once(directory/'result.json',result);ledger(result)
    print(name,m['macro_f05'],'delta',m['macro_f05']-base['macro_f05'],flush=True)
    return result

def train_models():
    if not (OUT/'error_analysis.json').exists():raise RuntimeError('Error analysis required before model search')
    train=role_data(0);valid=role_data(1)
    error=json.loads((OUT/'error_analysis.json').read_text());selected=error['selected_nonredundant']
    configs=[('v1_uniform',V1,{},True),('v1_hard',V1,{},False),
      ('v2_names',V1+GROUPS['name'],{},False),
      ('v2_names_reduced',V1+[x for x in GROUPS['name'] if x in selected],{},False),
      ('v2_address',V1+GROUPS['address'],{},False),
      ('v2_script',V1+GROUPS['script'],{},False),
      ('v2_cross',V1+GROUPS['cross'],{},False),
      ('v2_entity',V1+GROUPS['entity'],{},False),
      ('v2_compact',V1+selected,{},False)]
    bm,_=metrics(valid,valid['baseline'])
    baseline={'experiment_id':'frozen_v1_selection','feature_set':V1,'model':'Frozen V1',
      'training_population':'historical frozen model_fit tier B; rows not reopened',
      'candidate_policy':'v1_plus_all frozen','threshold_policy':'global >=0.61',
      'candidate_count':len(valid['y']),'metrics':bm,'artifact_SHA':sha(ROOT/'work/final_matcher_model.txt'),
      'runtime':0.,'status':'MODEL_SELECTION_ONLY'}
    if not (OUT/'frozen_selection.json').exists():
        write_once(OUT/'frozen_selection.json',baseline);ledger(baseline)
    results=[]
    for name,cols,params,uniform in configs:results.append(fit_one(name,cols,train,valid,params,uniform,rounds=350))
    # Feature selection is frozen before the small architecture search.
    best=select_simple_model(results)
    columns=best['feature_set']
    freeze=OUT/'selected_features.json'
    if not freeze.exists():write_once(freeze,{'chosen_ablation':best['experiment_id'],'features':columns,'selection_role':1})
    for name,params in [('v2_wider',{'num_leaves':63,'max_depth':10,'min_data_in_leaf':50,'lambda_l2':2.}),
                        ('v2_regularized',{'num_leaves':31,'max_depth':7,'min_data_in_leaf':200,'lambda_l1':1.,'lambda_l2':10.})]:
        results.append(fit_one(name,columns,train,valid,params,rounds=550))
    results.append(fit_xgb(columns,train,valid))
    best=select_simple_model([r for r in results if r['feature_set']==columns])
    if best['metrics']['macro_f05']-bm['macro_f05']<.001:best=baseline
    write_once(OUT/'selected_model.json',best)

def fit_xgb(columns,train,valid):
    import xgboost as xgb
    allowed=set(research_ids(ROOT))
    for d in (train,valid):assert_authorized(d['meta']['source1_entity_id'].to_pylist(),allowed)
    name='v2_xgboost';directory=OUT/'models'/name;directory.mkdir(parents=True,exist_ok=True)
    if (directory/'result.json').exists():return json.loads((directory/'result.json').read_text())
    start=time.perf_counter();idx=[FEATURES.index(x) for x in columns]
    dt=xgb.QuantileDMatrix(np.asarray(train['X'][:,idx],np.float32),label=train['y'],max_bin=64,nthread=2)
    dv=xgb.QuantileDMatrix(np.asarray(valid['X'][:,idx],np.float32),label=valid['y'],max_bin=64,nthread=2,ref=dt)
    def metric(pred,data):return 'macro_f05',metrics(valid,pred)[0]['macro_f05']
    params={'objective':'binary:logistic','tree_method':'hist','max_depth':7,'eta':.05,'min_child_weight':20,
      'subsample':.9,'colsample_bytree':.9,'lambda':5.,'nthread':2,'seed':42,'max_bin':64,'disable_default_eval_metric':1}
    model=xgb.train(params,dt,num_boost_round=450,evals=[(dv,'selection')],custom_metric=metric,
      maximize=True,early_stopping_rounds=40,verbose_eval=50)
    model=model[:model.best_iteration+1];model.save_model(directory/'model.ubj')
    scores=model.predict(dv);np.save(directory/'selection_scores.npy',scores)
    m,f=metrics(valid,scores);_,bf=metrics(valid,valid['baseline'])
    result={'experiment_id':name,'feature_set':columns,'model':'XGBoost','parameters':params,
      'training_population':'research internal train','selection_population':'research internal model_select',
      'candidate_policy':'v1_plus_all frozen','threshold_policy':'global >=0.61','candidate_count':len(scores),
      'metrics':m,'paired_vs_frozen':paired((f-bf)[valid['active']]),'runtime':time.perf_counter()-start,
      'artifact_SHA':sha(directory/'model.ubj'),'status':'MODEL_SELECTION_ONLY'}
    write_once(directory/'result.json',result);ledger(result)
    print(name,m['macro_f05'],flush=True)
    return result

def score_selected(d):
    selected=validate_selected_artifacts(require_policy=d.get('role')==3)
    return score_artifact(d,selected)


def score_artifact(d,selected):
    directory=OUT/'models'/selected['experiment_id']
    if selected['model']=='Frozen V1':return d['baseline'].copy()
    filename={'LightGBM':'model.txt','XGBoost':'model.ubj','Ensemble':'model.json'}[selected['model']]
    if sha(directory/filename)!=selected['artifact_SHA']:raise PermissionError('Scoring model artifact changed')
    if selected['model']=='Ensemble':
        result=np.zeros(len(d['y']),np.float32)
        for component in json.loads((directory/'model.json').read_text())['components']:
            if sha(OUT/component['artifact'])!=component['artifact_SHA']:raise PermissionError('Scoring ensemble component changed')
            result+=np.float32(component['weight'])*score_native(d,component['model'],OUT/component['artifact'],component['feature_set'])
        return result
    return score_native(d,selected['model'],directory/('model.txt' if selected['model']=='LightGBM' else 'model.ubj'),selected['feature_set'])


def score_native(d,kind,path,columns):
    if kind=='LightGBM':return predict_lgb(lgb.Booster(model_file=str(path)),d['X'],columns)
    import xgboost as xgb
    model=xgb.Booster();model.load_model(path);model.set_param({'nthread':2});idx=[FEATURES.index(x) for x in columns]
    out=np.empty(len(d['y']),np.float32)
    for start in range(0,len(out),100000):out[start:start+100000]=model.inplace_predict(np.asarray(d['X'][start:start+100000,idx]))
    return out

def run(stage):
    if stage=='errors':diagnostic_errors()
    elif stage=='train':train_models()
    elif stage in ('decision','assessment','reproduce'):
        from .policies import run as policy_run
        policy_run(stage)
    elif stage=='report':
        from .report import run as report_run
        report_run()
