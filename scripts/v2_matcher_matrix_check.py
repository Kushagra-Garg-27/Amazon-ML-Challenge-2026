"""Check frozen-model replay from already-authorized train/selection matrices."""
from pathlib import Path
import sys,json
import numpy as np
import lightgbm as lgb
import pyarrow.parquet as pq
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.matcher_v2.data import OUT,V1,FEATURES,write_once
from er.matcher_v2.experiments import predict_lgb
from er.matcher_v2.access import research_ids,assert_authorized
allowed=set(research_ids(ROOT));model=lgb.Booster(model_file=str(ROOT/'work/final_matcher_model.txt'));result={}
for role in ('train','model_select'):
    directory=OUT/'matrices'/role
    x=np.load(directory/'X.npy',mmap_mode='r');meta=pq.read_table(directory/'metadata.parquet')
    assert_authorized(meta['source1_entity_id'].to_pylist(),allowed)
    p=predict_lgb(model,x[:100000],V1);baseline=np.asarray(meta['v1_score'])[:len(p)]
    label=np.asarray(meta['label'])[:len(p)]
    result[role]={'max_frozen_score_difference':float(np.max(abs(p-baseline))),
      'exact':bool(np.array_equal(p,baseline)),'rows':len(x),'sampled_rows':len(p),
      'positive_name_mean':float(x[:len(p),FEATURES.index('name_ratio')][label==1].mean()),
      'negative_name_mean':float(x[:len(p),FEATURES.index('name_ratio')][label==0].mean())}
write_once(OUT/'matrix_replay_check.json',result)
print(json.dumps(result,indent=2))
