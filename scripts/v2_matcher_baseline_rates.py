"""Append clarified all-S1 baseline rates; preserve the initial audit unchanged."""
from pathlib import Path
import sys
import numpy as np
import pyarrow.parquet as pq
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.matcher_v2.data import OUT,write_once
from er.matcher_v2.access import research_ids,assert_authorized,sha

allowed=set(research_ids(ROOT));result={}
for name in ('v1','plus_all'):
    path=OUT/f'step0_{name}_s1.parquet';table=pq.read_table(path)
    assert_authorized(table['s1'].to_pylist(),allowed)
    truth=np.asarray(table['truth_n']);pred=np.asarray(table['pred_n']);multi=truth>1
    result[name]={'underprediction_rate':float((pred<truth).mean()),
      'overprediction_rate':float((pred>truth).mean()),
      'multimatch_underprediction_rate':float((pred[multi]<truth[multi]).mean()),
      'multimatch_overprediction_rate':float((pred[multi]>truth[multi]).mean()),
      's1_prediction_sha256':sha(path)}
write_once(OUT/'baseline_rates_all_s1.json',{'metrics':result,
  'clarification':'Initial baseline_reconstruction supplementary rates use truth>1 only. These rates include all 100000 S1; original artifact retained.',
  'status':'PASS','authorized_population':'v2_candidate_research'})
print(result)
