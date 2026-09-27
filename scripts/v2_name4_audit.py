"""Independent label-free structural audit of name4 candidate artifact."""
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
    path=R/'name_char4_manifest.json'
    manifest=json.loads(path.read_text())
    if manifest['status']!='GENERATED_CHECKSUMMED_PENDING_AUDIT':
        raise RuntimeError('Pass not ready for independent structural audit')
    for part in manifest['parts']:
        if sha(ROOT/part['path'])!=part['sha256']:
            raise RuntimeError('Candidate shard checksum mismatch')
    c=connect('name_char4_audit')
    glob=(R/'name_char4_candidates/*.parquet').as_posix()
    c.execute(f"CREATE TEMP VIEW pairs AS SELECT * FROM read_parquet('{glob}')")
    c.execute("""CREATE TEMP VIEW targets AS SELECT entity_id mid,'s2' src,country_norm cc FROM read_parquet('work/keys/train_s2.parquet')
      UNION ALL SELECT entity_id,'s3',country_norm FROM read_parquet('work/keys/train_s3.parquet')""")
    row=c.sql("""SELECT count(*) n,count(DISTINCT(source1_entity_id,target_entity_id)) identities,
      count(*) FILTER(WHERE r.entity_id IS NULL) outside_research,
      count(*) FILTER(WHERE t.mid IS NULL) absent_target,
      count(*) FILTER(WHERE s.country_norm<>t.cc) country_mismatch,
      count(*) FILTER(WHERE p.target_source<>t.src) source_mismatch,
      count(*) FILTER(WHERE p.provenance<>16) provenance_error,
      count(*) FILTER(WHERE p.source_rank<1 OR p.source_rank>25) rank_error,
      count(*) FILTER(WHERE substr(md5(p.source1_entity_id),1,1)<>regexp_extract(p.filename,'([0-9a-f])\\.parquet$',1)) shard_error
      FROM read_parquet('work/v2_research/name_char4_candidates/*.parquet',filename=true) p
      LEFT JOIN read_parquet('work/v2_candidate_research.parquet') r ON p.source1_entity_id=r.entity_id
      LEFT JOIN read_parquet('work/keys/train_s1.parquet') s ON p.source1_entity_id=s.entity_id
      LEFT JOIN targets t ON p.target_entity_id=t.mid""").fetchone()
    keys=['rows','identities','outside_research','absent_target','country_mismatch','source_mismatch','provenance_error','rank_error','shard_error']
    audit=dict(zip(keys,row));audit['duplicates']=audit['rows']-audit['identities']
    quota=c.sql("""SELECT coalesce(max(n),0),count(*) FILTER(WHERE n>25) FROM
      (SELECT source1_entity_id,target_source,count(*) n FROM pairs GROUP BY 1,2)""").fetchone()
    audit['max_per_s1_target_source']=quota[0];audit['quota_violations']=quota[1]
    audit['source_country_rows']=[dict(zip(['source','country','rows'],r)) for r in c.sql("""SELECT target_source,s.country_norm,count(*)
      FROM pairs p JOIN read_parquet('work/keys/train_s1.parquet') s
      ON p.source1_entity_id=s.entity_id GROUP BY 1,2 ORDER BY 1,2""").fetchall()]
    bad=[k for k in ('duplicates','outside_research','absent_target','country_mismatch','source_mismatch','provenance_error','rank_error','shard_error','quota_violations') if audit[k]]
    if audit['rows']!=manifest['rows'] or bad:
        write_json(R/'name_char4_structural_audit.json',{'status':'FAIL','audit':audit,'violations':bad})
        raise RuntimeError(f'Name4 structural violations: {bad}')
    write_json(R/'name_char4_structural_audit.json',{'status':'PASS','audit':audit,
        'complete_target_corpus_rows':manifest['complete_target_rows'],'sealed_s1_not_in_candidate_source':True,
        'labels_read':False})
    manifest['status']='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED'
    manifest['structural_audit_sha256']=sha(R/'name_char4_structural_audit.json')
    write_json(path,manifest)
    log('name_char4_structural_audit_complete',command='.venv\\Scripts\\python.exe -B scripts\\v2_name4_audit.py',
        v2_research_label_read=False,manifest_sha256=sha(path),audit_sha256=manifest['structural_audit_sha256'])
    print(json.dumps(audit,indent=2))


if __name__=='__main__':
    with Monitor(R/'tmp/name_char4_audit') as m: run()
    write_json(R/'name_char4_audit_resources.json',m.result())
