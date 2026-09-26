"""One bounded native-LightGBM smoke comparison."""
from __future__ import annotations
import json,time
from pathlib import Path
import duckdb
import lightgbm as lgb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from er.features.schema import FEATURE_NAMES
from .baseline import load_matrix,metrics,truth_for

def main():
 started=time.time(); sample='work/negative_samples/hybrid_source_balanced.parquet'
 _,_,_,x=load_matrix(sample); c=duckdb.connect(); y=np.asarray(c.sql(f"select y from read_parquet('{sample}') order by source1_entity_id,target_entity_id").fetchnumpy()['y']).astype(np.uint8); c.close()
 train=lgb.Dataset(x,label=y,feature_name=list(FEATURE_NAMES),free_raw_data=True)
 params={'objective':'binary','metric':'binary_logloss','learning_rate':.08,'num_leaves':15,'min_data_in_leaf':100,'feature_fraction':.8,'bagging_fraction':.8,'bagging_freq':1,'seed':42,'feature_fraction_seed':42,'bagging_seed':42,'num_threads':2,'verbosity':-1}
 t=time.time(); model=lgb.train(params,train,num_boost_round=40); training=time.time()-t
 _,ids,mids,cx=load_matrix('work/feature_pilot_v1_final/model_calibration_*.parquet'); scores=model.predict(cx)
 c=duckdb.connect(); country=dict(c.sql("select entity_id,country_norm from read_parquet('work/feature_pilot_s1.parquet') where split='model_calibration'").fetchall()); truth=truth_for(country)
 c.execute("""CREATE TEMP TABLE fullgt AS SELECT source1_entity_id s1,trim(mid) mid FROM read_csv('dataset/train/train_ground_truth.tsv',delim='\t',header=true,quote='',all_varchar=true),unnest(string_split(matched_entity_ids,',')) u(mid) WHERE matched_entity_ids IS NOT NULL AND length(trim(mid))>0""")
 addr={(s,m):missing for s,m,missing in c.sql("""SELECT g.s1,g.mid,(length(s.addr_norm)=0 OR length(t.addr_norm)=0) FROM fullgt g JOIN read_parquet('work/feature_pilot_s1.parquet') p ON g.s1=p.entity_id AND p.split='model_calibration' JOIN read_parquet('work/keys/train_s1.parquet') s ON g.s1=s.entity_id JOIN (SELECT entity_id,addr_norm FROM read_parquet('work/keys/train_s2.parquet') UNION ALL SELECT entity_id,addr_norm FROM read_parquet('work/keys/train_s3.parquet')) t ON g.mid=t.entity_id""").fetchall()}; c.close()
 coarse=[i/20 for i in range(21)]; cr=[metrics(ids,mids,scores,truth,z,country,addr)[0] for z in coarse]; center=max(cr,key=lambda r:(r['macro_f0_5'],-r['threshold']))['threshold']; grid=sorted(set(max(0,min(1,center+d/100)) for d in range(-5,6))); rows=[metrics(ids,mids,scores,truth,z,country,addr)[0] for z in grid]; best=max(rows,key=lambda r:(r['macro_f0_5'],-r['threshold']))
 model.save_model('work/lightgbm_smoke_v1.txt'); pq.write_table(pa.table({'source1_entity_id':ids,'target_entity_id':mids,'score':scores.astype(np.float32)}),'work/lightgbm_smoke_scores.parquet',compression='zstd')
 out={'status':'completed_smoke_only','seed':42,'trees':40,'num_leaves':15,'threads':2,'training_rows':len(y),'positives':int(y.sum()),'training_seconds':training,'wall_seconds':time.time()-started,'best':best,'model_path':'work/lightgbm_smoke_v1.txt'}; Path('work/lightgbm_smoke.json').write_text(json.dumps(out,indent=2)+"\n"); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
