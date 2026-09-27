"""Label-free parity check for the arbitrary-candidate V2 feature adapter."""
import argparse,json,math,sys,time
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa

from common import ROOT,execute_gate,sha,write_new

sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.matcher_v2.data import V1,FEATURES,process_peak_rss
from er.matcher_v2.experiments import features_guard,validate_selected_artifacts
from er.matcher_v2.inference import adapt_table,score_v1_table


def run(args):
    output=(ROOT/args.output).resolve()
    if not output.is_relative_to(ROOT.resolve()) or output.exists():raise FileExistsError('New parity receipt required')
    evidence={name:json.loads((ROOT/'work/v2_matcher_sprint_r1'/name).read_text())
      for name in ('assessment.json','reproduction.json','leakage_audit.json')}
    if evidence['assessment.json'].get('status')!='COMPLETE_ONE_TIME_INTERNAL_ASSESSMENT':raise PermissionError('Assessment absent')
    if evidence['reproduction.json'].get('status')!='PASS' or evidence['leakage_audit.json'].get('status')!='PASS':
        raise PermissionError('Reproduction and audit required')
    features_guard();validate_selected_artifacts(require_policy=True)
    started=time.perf_counter();c=duckdb.connect()
    c.execute("SET threads=2; SET memory_limit='1000MB'; SET preserve_insertion_order=false")
    glob=(ROOT/'work/v2_matcher_sprint_r1/features/*.parquet').as_posix()
    ids=[x[0] for x in c.execute(f"""SELECT DISTINCT source1_entity_id FROM read_parquet('{glob}')
      WHERE role=3 ORDER BY md5(source1_entity_id||':v2-adapter-parity') LIMIT ?""",[args.s1]).fetchall()]
    if len(ids)!=args.s1:raise RuntimeError('Insufficient assessment entities for parity check')
    c.register('chosen',pa.table({'source1_entity_id':ids}))
    expected=c.execute(f"""SELECT f.* FROM read_parquet('{glob}') f JOIN chosen USING(source1_entity_id)
      ORDER BY source1_entity_id,target_entity_id""").to_arrow_table()
    sources=c.execute("""SELECT s.* FROM read_parquet('work/v2_matcher_sprint_r1/source_records.parquet') s
      JOIN chosen ON s.entity_id=chosen.source1_entity_id""").to_arrow_table()
    c.register('mids',pa.table({'entity_id':expected['target_entity_id']}))
    targets=c.execute("""SELECT t.* FROM (SELECT * FROM read_parquet('work/keys/train_s2.parquet')
      UNION ALL BY NAME SELECT * FROM read_parquet('work/keys/train_s3.parquet')) t JOIN mids USING(entity_id)""").to_arrow_table()
    source_records={row['entity_id']:row for row in sources.to_pylist()}
    target_records={row['entity_id']:row for row in targets.to_pylist()}
    weights={}
    for country in sorted({row['country_norm'] for row in source_records.values()}):
        weights[country]={}
        for field,filename in (('name','df_name.parquet'),('addr','df_addr.parquet')):
            weights[country][field]={tok:math.log1p(10320219/df) for tok,df in c.execute(
              f"SELECT tok,df FROM read_parquet('work/freeze_gate/{filename}') WHERE cc=?",[country]).fetchall()}
    c.close()
    v1=expected.select(['source1_entity_id','target_entity_id',*V1])
    scores=score_v1_table(v1,ROOT/'work/final_matcher_model.txt',ROOT/'work/final_matcher_policy.json',
      '76ff78a7cc97e40174b6d631a6eaa062c786f0480c6693fd6387e90be6761d21')
    adapted=adapt_table(v1,source_records,target_records,weights,scores)
    differences={}
    for name in FEATURES:
        actual=np.asarray(adapted[name],dtype=np.float32);prior=np.asarray(expected[name],dtype=np.float32)
        if not np.array_equal(actual,prior):
            differences[name]={'differing_rows':int((actual!=prior).sum()),
              'max_abs_difference':float(np.max(np.abs(actual-prior)))}
    prior_scores=np.asarray(expected['v1_score'],dtype=np.float32)
    if not np.array_equal(scores,prior_scores) or differences:
        raise RuntimeError('Arbitrary-candidate adapter parity failed: '+json.dumps(differences))
    result={'status':'PASS','scope':'label-free complete candidate neighborhoods from assessment role',
      'assessment_outcomes_used':False,'assessment_labels_used':False,'s1':len(ids),'candidate_rows':len(expected),
      'teacher_scores_exact':True,'all_100_features_exact':True,'feature_differences':differences,
      'adapter_sha256':sha(ROOT/'code/business_entity_resolution/src/er/matcher_v2/inference.py'),
      'teacher_model_sha256':sha(ROOT/'work/final_matcher_model.txt'),
      'feature_manifest_sha256':sha(ROOT/'work/v2_matcher_sprint_r1/features/manifest.json'),
      'decision_policy_sha256':sha(ROOT/'work/v2_matcher_sprint_r1/decision_policy_refined.json'),
      'runtime_seconds':time.perf_counter()-started,'peak_process_rss_bytes':process_peak_rss(),
      'candidate_contract':'complete same-S1 neighborhoods supplied together; pair IDs unique; normalized records and complete-target DF weights required'}
    write_new(output,result);print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--s1',type=int,default=64)
    p.add_argument('--output',default='work/v3_research_r1/v2_adapter_parity.json')
    args=p.parse_args();execute_gate(args);run(args)
