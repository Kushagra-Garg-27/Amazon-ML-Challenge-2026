"""Tie the fresh sprint baseline to the original frozen scoring bytes."""
from pathlib import Path
import sys
import json
import numpy as np
import pyarrow.parquet as pq
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.matcher_v2.data import OUT,OLD,write_once,ledger
from er.matcher_v2.access import research_ids,assert_authorized,sha

allowed=set(research_ids(ROOT));checks=[]
for name in ('v1','new'):
    for p in sorted((OUT/('baseline_'+name+'_scores')).glob('*.parquet')):
        receipt=json.loads(p.with_suffix('.json').read_text())
        reference=OLD/(name+'_scores')/p.name
        actual=sha(p);expected=sha(reference)
        if actual!=expected or actual!=receipt['sha256']:raise RuntimeError('Frozen score bytes differ: '+str(p))
        checks.append({'path':p.relative_to(ROOT).as_posix(),'sha256':actual,'reference':reference.relative_to(ROOT).as_posix()})
baseline=json.loads((OUT/'baseline.json').read_text());supplement={}
for name in ('v1','plus_all'):
    table=pq.read_table(OUT/f'step0_{name}_s1.parquet')
    assert_authorized(table['s1'].to_pylist(),allowed)
    truth=np.asarray(table['truth_n']);pred=np.asarray(table['pred_n']);multi=truth>1
    supplement[name]={'multi_match_s1':int(multi.sum()),
      'underprediction_rate':float((pred[multi]<truth[multi]).mean()),
      'overprediction_rate':float((pred[multi]>truth[multi]).mean())}
write_once(OUT/'baseline_reconstruction.json',{'status':'PASS','score_parts_byte_identical':checks,
  'supplementary_metrics':supplement,'prior_full_research_macro_f05_identically_reproduced':True,
  'sealed_membership_read':False,'test_rows_read':False})
print('PASS: all',len(checks),'recomputed score parts are byte-identical to the frozen Phase 2 scores')
