"""Pilot deterministic and logistic baselines with calibration-only thresholds."""
from __future__ import annotations
import argparse,datetime,hashlib,json,time
from pathlib import Path
import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from er.evaluate import f_beta_entity
from er.features.schema import FEATURE_NAMES
from .sampling import build as build_samples

SEED=42

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(8<<20),b''): h.update(b)
 return h.hexdigest()

def deterministic_score(row):
 return min(1.0,0.30*row['exact_address_norm']+0.24*row['exact_name_nosuffix']+0.12*row['exact_name_sorted']+0.10*row['name_token_set_ratio']+0.08*row['address_token_set_ratio']+0.06*row['digit_sequence_equal']+0.05*(row['retrieval_pass_count']>=2)+0.05*row['exact_postal_token'])

def sigmoid(z):
 z=np.clip(z,-30,30); return 1.0/(1.0+np.exp(-z))

def fit_logistic(x,y,seed=SEED,epochs=18,batch=8192,learning_rate=.08,l2=1e-4):
 """Deterministic bounded mini-batch logistic regression.

 Used because local Windows application control blocks sklearn's compiled
 ``_cd_fast`` DLL. The persisted model is still a plain linear logit.
 """
 rng=np.random.default_rng(seed); mean=x.mean(0,dtype=np.float64); scale=x.std(0,dtype=np.float64); scale[scale<1e-6]=1.0
 xs=((x-mean)/scale).astype(np.float32); w=np.zeros(xs.shape[1],np.float64); b=0.0
 pos=max(1,int(y.sum())); neg=max(1,len(y)-pos); pos_weight=neg/pos
 for _ in range(epochs):
  order=rng.permutation(len(y))
  for start in range(0,len(y),batch):
   ix=order[start:start+batch]; xb=xs[ix]; yb=y[ix].astype(np.float64); sw=np.where(yb>0,pos_weight,1.0)
   err=(sigmoid(xb@w+b)-yb)*sw; denom=sw.sum()
   w-=learning_rate*((xb.T@err)/denom+l2*w); b-=learning_rate*err.sum()/denom
 return mean,scale,w,b

def load_matrix(path_glob,where='true'):
 c=duckdb.connect(); cols=','.join(FEATURE_NAMES)
 table=c.sql(f"SELECT source1_entity_id,target_entity_id,{cols} FROM read_parquet('{path_glob}') WHERE {where} ORDER BY 1,2").fetch_arrow_table(); c.close()
 x=np.column_stack([np.asarray(table[x]).astype(np.float32) for x in FEATURE_NAMES]); ids=np.asarray(table['source1_entity_id']).astype(str); mids=np.asarray(table['target_entity_id']).astype(str)
 return table,ids,mids,x

def truth_for(entities):
 wanted=set(entities); truth={x:set() for x in wanted}
 with open('dataset/train/train_ground_truth.tsv',encoding='utf-8') as f:
  next(f)
  for line in f:
   s,_,rest=line.rstrip('\n').partition('\t')
   if s in wanted: truth[s]=set(rest.split(',')) if rest.strip() else set()
 return truth

def metrics(ids,mids,scores,truth,threshold,country,addr_missing):
 pred={x:set() for x in truth}
 for s,m,v in zip(ids,mids,scores):
  if v>=threshold: pred[s].add(m)
 fs=[]; ps=[]; rs=[]; tp=fp=fn=0
 for s,t in truth.items():
  p=pred[s]; inter=len(p&t); fs.append(f_beta_entity(p,t)); ps.append(inter/len(p) if p else (1.0 if not t else 0.0)); rs.append(inter/len(t) if t else 1.0); tp+=inter; fp+=len(p-t); fn+=len(t-p)
 def src_pr(prefix):
  a={(s,m) for s,v in pred.items() for m in v if m.startswith(prefix)}; b={(s,m) for s,v in truth.items() for m in v if m.startswith(prefix)}; z=len(a&b); return (z/len(a) if a else 0,z/len(b) if b else 0)
 out={'threshold':threshold,'macro_f0_5':float(np.mean(fs)),'mean_entity_precision':float(np.mean(ps)),'mean_entity_recall':float(np.mean(rs)),'pair_precision':tp/(tp+fp) if tp+fp else 0,'pair_recall':tp/(tp+fn) if tp+fn else 0,'avg_predicted':(tp+fp)/len(truth),'singleton_accuracy':sum(not pred[s] for s,t in truth.items() if not t)/max(1,sum(not t for t in truth.values())),'empty_prediction_rate':sum(not p for p in pred.values())/len(pred)}
 out['s2_precision'],out['s2_recall']=src_pr('S2-'); out['s3_precision'],out['s3_recall']=src_pr('S3-')
 for cc in sorted(set(country.values())): out[f'{cc}_macro_f0_5']=float(np.mean([f_beta_entity(pred[s],truth[s]) for s in truth if country[s]==cc]))
 for key,val in [('address_present',False),('address_missing',True)]:
  pairs={(s,m) for s,t in truth.items() for m in t if addr_missing.get((s,m),True)==val}; out[f'{key}_recall']=len(pairs & {(s,m) for s,p in pred.items() for m in p})/len(pairs) if pairs else 0
 return out,pred

def run(output_dir: Path):
 output_dir.mkdir(exist_ok=True); started=time.time(); labels=Path('work/feature_pilot_labels.parquet')
 samples=build_samples('work/feature_pilot_v1_final/model_train_*.parquet',labels,Path('work/negative_samples'))
 sample=Path(samples['hybrid_source_balanced']['path']); table,train_ids,train_mids,x=load_matrix(sample.as_posix()); c=duckdb.connect(); y=np.asarray(c.sql(f"select y from read_parquet('{sample.as_posix()}') order by source1_entity_id,target_entity_id").fetchnumpy()['y']).astype(np.uint8); c.close()
 t0=time.time(); mean,scale,coef,intercept=fit_logistic(x,y); train_seconds=time.time()-t0
 ftable,ids,mids,cx=load_matrix('work/feature_pilot_v1_final/model_calibration_*.parquet'); det=np.array([deterministic_score({n:ftable[n][i].as_py() for n in FEATURE_NAMES}) for i in range(len(ids))],dtype=np.float32); logistic=sigmoid(((cx-mean)/scale)@coef+intercept)
 c=duckdb.connect(); countries=dict(c.sql("select entity_id,country_norm from read_parquet('work/feature_pilot_s1.parquet') where split='model_calibration'").fetchall()); entities=sorted(countries); truth=truth_for(entities)
 c.execute("""CREATE TEMP TABLE fullgt AS SELECT source1_entity_id s1,trim(mid) mid FROM read_csv('dataset/train/train_ground_truth.tsv',delim='\t',header=true,quote='',all_varchar=true),unnest(string_split(matched_entity_ids,',')) u(mid) WHERE matched_entity_ids IS NOT NULL AND length(trim(mid))>0""")
 addr={(s,m):missing for s,m,missing in c.sql("""SELECT g.s1,g.mid,(length(s.addr_norm)=0 OR length(t.addr_norm)=0) missing FROM fullgt g JOIN read_parquet('work/feature_pilot_s1.parquet') p ON g.s1=p.entity_id AND p.split='model_calibration' JOIN read_parquet('work/keys/train_s1.parquet') s ON g.s1=s.entity_id JOIN (SELECT entity_id,addr_norm FROM read_parquet('work/keys/train_s2.parquet') UNION ALL SELECT entity_id,addr_norm FROM read_parquet('work/keys/train_s3.parquet')) t ON g.mid=t.entity_id""").fetchall()}; c.close()
 all_rows=[]; best={}
 for name,scores in [('deterministic',det),('logistic',logistic)]:
  coarse=np.arange(0,1.0001,.05); coarse_rows=[metrics(ids,mids,scores,truth,float(z),countries,addr)[0] for z in coarse]; cb=max(coarse_rows,key=lambda r:(r['macro_f0_5'],-r['threshold']))
  grid=sorted(set(max(0,min(1,cb['threshold']+d)) for d in np.arange(-.05,.051,.01))); rows=[metrics(ids,mids,scores,truth,float(z),countries,addr)[0] for z in grid]; b=max(rows,key=lambda r:(r['macro_f0_5'],-r['threshold'])); best[name]=b; [r.update(model=name) for r in rows]; all_rows+=rows
 curve=Path('work/baseline_threshold_curve.parquet'); pq.write_table(pa.Table.from_pylist(all_rows),curve,compression='zstd')
 score_table=pa.table({'source1_entity_id':ids,'target_entity_id':mids,'deterministic_score':det,'logistic_score':logistic.astype(np.float32),'y':np.array([int(m in truth.get(s,set())) for s,m in zip(ids,mids)],dtype=np.uint8)})
 pq.write_table(score_table,'work/baseline_calibration_scores.parquet',compression='zstd')
 model_json={'format':'plain-json-linear-logit-v1','implementation':'numpy deterministic mini-batch; sklearn compiled DLL blocked by application control','seed':SEED,'features':list(FEATURE_NAMES),'mean':mean.tolist(),'scale':scale.tolist(),'coefficient':coef.tolist(),'intercept':float(intercept),'classes':[0,1],'training_seconds':train_seconds,'training_rows':len(y),'positives':int(y.sum())}; Path('work/logistic_baseline_v1.json').write_text(json.dumps(model_json,indent=2)+"\n")
 oracle_scores=np.array([1.0 if m in truth[s] else 0.0 for s,m in zip(ids,mids)]); oracle=metrics(ids,mids,oracle_scores,truth,.5,countries,addr)[0]
 report={'samples':samples,'candidate_oracle':oracle,'best':best,'model':{k:model_json[k] for k in ('seed','training_seconds','training_rows','positives')},'wall_seconds':time.time()-started,'calibration_s1':len(entities),'calibration_candidates':len(ids)}
 Path('work/baseline_results.json').write_text(json.dumps(report,indent=2)+"\n")
 return report,truth,ids,mids,logistic,best['logistic']['threshold']

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--output-dir',type=Path,default=Path('work')); a=ap.parse_args(); report,truth,ids,mids,scores,thr=run(a.output_dir); print(json.dumps(report,indent=2))
if __name__=='__main__': main()
