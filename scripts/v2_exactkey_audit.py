"""Independent label-free structural audit for bounded exact-key passes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.runtime import R,W,sha,write_json,log,connect,populations,Monitor


def run(kind):
    os.chdir(ROOT)
    research,sealed=populations()
    path=R/f'{kind}_manifest.json';manifest=json.loads(path.read_text())
    if manifest['status']!='GENERATED_CHECKSUMMED_PENDING_AUDIT':
        raise RuntimeError('Candidate artifact not ready')
    for part in manifest['parts']:
        if sha(ROOT/part['path'])!=part['sha256']:
            raise RuntimeError('Candidate shard checksum mismatch')
    c=connect(f'{kind}_audit')
    glob=f'work/v2_research/{kind}_candidates/*.parquet'
    bit=manifest['config']['provenance_bit']
    quota=manifest['config'].get('per_target_source_quota',manifest['config'].get('per_s1_expansion_quota'))
    c.execute(f"CREATE TEMP VIEW pairs AS SELECT * FROM read_parquet('{glob}',filename=true)")
    c.execute("""CREATE TEMP VIEW targets AS SELECT entity_id mid,'s2' src,country_norm cc FROM read_parquet('work/keys/train_s2.parquet')
      UNION ALL SELECT entity_id,'s3',country_norm FROM read_parquet('work/keys/train_s3.parquet')""")
    row=c.sql(f"""SELECT count(*),count(DISTINCT(source1_entity_id,target_entity_id)),
      count(*) FILTER(WHERE r.entity_id IS NULL),count(*) FILTER(WHERE t.mid IS NULL),
      count(*) FILTER(WHERE s.country_norm<>t.cc),count(*) FILTER(WHERE p.target_source<>t.src),
      count(*) FILTER(WHERE p.provenance<>{bit}),
      count(*) FILTER(WHERE p.source_rank<1 OR p.source_rank>{quota}),
      count(*) FILTER(WHERE substr(md5(p.source1_entity_id),1,1)<>regexp_extract(p.filename,'([0-9a-f])\\.parquet$',1))
      FROM pairs p LEFT JOIN read_parquet('work/v2_candidate_research.parquet') r
      ON p.source1_entity_id=r.entity_id
      LEFT JOIN read_parquet('work/keys/train_s1.parquet') s
      ON p.source1_entity_id=s.entity_id LEFT JOIN targets t ON p.target_entity_id=t.mid""").fetchone()
    labels=['rows','identities','outside_research','absent_target','country_mismatch',
            'source_mismatch','provenance_error','rank_error','shard_error']
    audit=dict(zip(labels,row));audit['duplicates']=audit['rows']-audit['identities']
    max_count,violations=c.sql(f"""SELECT coalesce(max(n),0),count(*) FILTER(WHERE n>{quota}) FROM
      (SELECT source1_entity_id,target_source,count(*) n FROM pairs GROUP BY 1,2)""").fetchone()
    audit['max_per_s1_source']=max_count;audit['quota_violations']=violations
    if kind=='sister_expansion':
        opposite=c.sql("""SELECT count(*) FROM pairs p
          JOIN read_parquet('work/v2_research/sister_one_seed.parquet') seed
            ON p.source1_entity_id=seed.s1
          WHERE substr(p.target_entity_id,1,2)=substr(seed.seed_mid,1,2)
             OR p.target_entity_id=seed.seed_mid""").fetchone()[0]
        audit['same_source_or_seed_violations']=opposite
    errors=[name for name in ('duplicates','outside_research','absent_target','country_mismatch',
      'source_mismatch','provenance_error','rank_error','shard_error','quota_violations') if audit[name]]
    if audit.get('same_source_or_seed_violations'):errors.append('same_source_or_seed_violations')
    if errors or audit['rows']!=manifest['rows']:
        write_json(R/f'{kind}_structural_audit.json',{'status':'FAIL','audit':audit,'violations':errors})
        raise RuntimeError(f'{kind} structural audit failed: {errors}')
    write_json(R/f'{kind}_structural_audit.json',{'status':'PASS','audit':audit,
      'labels_read':False,'complete_target_corpus_rows':manifest['target_rows']})
    manifest['status']='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED'
    manifest['structural_audit_sha256']=sha(R/f'{kind}_structural_audit.json')
    write_json(path,manifest)
    log(f'{kind}_structural_audit_complete',command=f'.venv\\Scripts\\python.exe -B scripts\\v2_exactkey_audit.py --pass {kind}',
      v2_research_label_read=False,audit_sha256=manifest['structural_audit_sha256'])
    print(json.dumps(audit,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--pass',dest='kind',required=True,choices=['acronym','postal_like_numeric','address_char4','sister_expansion'])
    args=parser.parse_args()
    with Monitor(R/f'tmp/{args.kind}_audit') as m:run(args.kind)
    write_json(R/f'{args.kind}_audit_resources.json',m.result())
