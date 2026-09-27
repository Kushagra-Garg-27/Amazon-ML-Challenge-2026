"""Independent label-free structural audit of the frozen V1 research baseline."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.runtime import R,W,sha,write_json,log,connect,populations,Monitor


def run():
    os.chdir(ROOT)
    research,sealed=populations()
    m=json.loads((R/'v1_baseline_manifest.json').read_text())
    if m['status']!='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED' or m['gt_used']:
        raise PermissionError('Frozen V1 baseline not complete and label-free')
    for input in m['inputs'].values():
        if sha(ROOT/input['path'])!=input['sha256']:
            raise RuntimeError('Frozen input changed')
    for part in m['parts']:
        if sha(ROOT/part['path'])!=part['sha256']:
            raise RuntimeError('Candidate part checksum mismatch')
        for rank in part['rank_artifacts']:
            if sha(ROOT/rank['path'])!=rank['sha256']:
                raise RuntimeError('V1 rank evidence checksum mismatch')
    c=connect('baseline_independent_audit')
    c.execute("CREATE TEMP VIEW candidates AS SELECT * FROM read_parquet('work/v2_research/v1_candidates/*.parquet')")
    c.execute("""CREATE TEMP VIEW targets AS SELECT entity_id mid,country_norm country FROM read_parquet('work/keys/train_s2.parquet')
       UNION ALL SELECT entity_id,country_norm FROM read_parquet('work/keys/train_s3.parquet')""")
    row=c.sql("""SELECT count(*) candidates,count(*)-count(DISTINCT(source1_entity_id,target_entity_id)) duplicate_pairs,
      count(*) FILTER(WHERE r.entity_id IS NULL) outside_research,
      count(*) FILTER(WHERE t.mid IS NULL) missing_target,
      count(*) FILTER(WHERE k.country_norm!=t.country) country_mismatch
      FROM candidates x LEFT JOIN read_parquet('work/v2_candidate_research.parquet') r ON x.source1_entity_id=r.entity_id
      LEFT JOIN targets t ON x.target_entity_id=t.mid
      LEFT JOIN read_parquet('work/keys/train_s1.parquet') k ON k.entity_id=x.source1_entity_id""").fetchone()
    if row!=(m['candidates'],0,0,0,0): raise RuntimeError('Independent candidate audit failed: '+repr(row))
    source_country=[dict(zip(['country','target_source','candidate_pairs'],r)) for r in c.sql("""SELECT k.country_norm,substr(x.target_entity_id,1,2),count(*) FROM candidates x
      JOIN read_parquet('work/keys/train_s1.parquet') k ON k.entity_id=x.source1_entity_id GROUP BY 1,2 ORDER BY 1,2""").fetchall()]
    df={}
    for name in ('name','addr'):
        df[name]=[dict(zip(['country','keys','excluded_df_gt_2000','included_df_lte_2000','max_df'],r)) for r in c.sql(f"""SELECT cc,count(*),count(*) FILTER(WHERE df>2000),count(*) FILTER(WHERE df<=2000),max(df)
          FROM read_parquet('work/freeze_gate/df_{name}.parquet') GROUP BY 1 ORDER BY 1""").fetchall()]
    proof={'status':'PASS','label_access':False,'research_s1':len(research),
           'target_corpus':'complete training S2+S3; target table rows and checksums bound to baseline inputs',
           'source_rows':m['inputs']['train_s1']['rows'],
           'target_rows':m['inputs']['train_s2']['rows']+m['inputs']['train_s3']['rows'],
           'filters':'country equality only; V1 name/address retrieval and DF<=2000; no GT-target filter',
           'v1_passes':['exact_sorted_name','exact_normalized_address','name_token','address_token'],
           'source_quotas':{'S2':50,'S3':50},'heavy_sorted_cap':100,
           'candidate_count':row[0],'duplicate_pairs':row[1],
           'outside_research':row[2],'missing_targets':row[3],'country_mismatch':row[4],
           'candidate_by_source_country':source_country,'target_df_key_counts':df,
           'baseline_manifest_sha256':sha(R/'v1_baseline_manifest.json'),
           'rank_evidence_parts':sum(len(p['rank_artifacts']) for p in m['parts'])}
    write_json(R/'v1_structural_audit.json',proof)
    log('v1_baseline_independent_structural_audit',command='.venv\\Scripts\\python.exe -B scripts\\v2_baseline_audit.py',
        v2_research_label_read=False,audit_sha256=sha(R/'v1_structural_audit.json'))
    print(json.dumps(proof,indent=2))


if __name__=='__main__':
    with Monitor(R/'tmp/baseline_independent_audit') as m: run()
    write_json(R/'v1_structural_audit_resources.json',m.result())
