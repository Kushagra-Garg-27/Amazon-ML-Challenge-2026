"""Complete-truth entity classes, restricted to internal research train."""
from pathlib import Path
import sys,json
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.matcher_v2.data import OUT,FEATURES,connection,write_once
from er.matcher_v2.experiments import role_data

d=role_data(0);c=connection('error_supplement')
rows=c.sql(f"""SELECT a.entity_index,count(*) FILTER(WHERE starts_with(g.mid,'S2')) s2,
 count(*) FILTER(WHERE starts_with(g.mid,'S3')) s3
 FROM read_parquet('work/v2_research/research_gt.parquet') g
 JOIN read_parquet('{(OUT/'allocation.parquet').as_posix()}') a USING(s1)
 WHERE a.role=0 GROUP BY 1""").fetchall();c.close()
s2=np.zeros(100000,np.int32);s3=s2.copy()
for i,a,b in rows:s2[i]=a;s3[i]=b
if not np.array_equal(s2+s3,d['truth']):raise RuntimeError('Source prefix truth accounting differs')
pred=np.bincount(d['entity'][d['baseline']>=.61],minlength=100000)
result={}
for name,mask in [('underpredicted',pred<d['truth']),('overpredicted',pred>d['truth']),
  ('S2_only',(s2>0)&(s3==0)),('S3_only',(s3>0)&(s2==0)),('multisource',(s2>0)&(s3>0))]:
    pairmask=mask[d['entity']];features={}
    for feature in ('name_ratio','address_ratio','translit_ratio','cross_joint','addr_missing','source_balanced_rank'):
        v=np.asarray(d['X'][:,FEATURES.index(feature)])[pairmask]
        features[feature]={'mean':float(v.mean()) if len(v) else None,'p10_p50_p90':np.quantile(v,[.1,.5,.9]).tolist() if len(v) else []}
    result[name]={'s1_from_complete_truth':int(mask[d['active']].sum()),'sampled_pairs':int(pairmask.sum()),'distributions':features}
write_once(OUT/'error_analysis_complete_truth.json',{'role':'internal research train only','classes':result,
  'purpose':'Supplement candidate-recovered source classes in error_analysis.json using all authorized training truth, including retrieval misses. No feature/model input uses this diagnostic.'})
print(json.dumps({k:v['s1_from_complete_truth'] for k,v in result.items()}))
