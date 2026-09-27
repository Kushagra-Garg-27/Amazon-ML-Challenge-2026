"""Bounded label-free acronym or postal-like numeric candidate retrieval."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.runtime import R,W,sha,write_json,log,connect,populations,Monitor

CAP=200
QUOTA=10
MAX_JOIN_ROWS=2_000_000
SHARDS='0123456789abcdef'


def run(kind):
    os.chdir(ROOT)
    research,sealed=populations()
    proof=json.loads((R/'other_preflight.json').read_text())
    if proof['status']!='FULL_TARGET_ALTERNATE_KEY_PREFLIGHT':
        raise PermissionError('Full-target key preflight missing')
    case=proof['cases'][kind]
    if sha(ROOT/case['target_df_path'])!=case['target_df_sha256']:
        raise RuntimeError('Target key DF changed')
    option=next(x for x in case['options'] if x['df_cap']==CAP)
    if option['estimated_join_rows']>MAX_JOIN_ROWS:
        raise RuntimeError('Preflight join estimate exceeds hard cap')
    c=connect(kind)
    c.execute("""CREATE TEMP VIEW targets AS SELECT entity_id mid,'s2' target_source,
      country_norm cc,name_norm nm,addr_norm addr,name_acronym ac,num_tokens nums
      FROM read_parquet('work/keys/train_s2.parquet') UNION ALL
      SELECT entity_id,'s3',country_norm,name_norm,addr_norm,name_acronym,num_tokens
      FROM read_parquet('work/keys/train_s3.parquet')""")
    c.execute("""CREATE TEMP VIEW sources AS SELECT k.entity_id s1,k.country_norm cc,
      k.name_norm nm,k.addr_norm addr,k.name_acronym ac,k.num_tokens nums
      FROM read_parquet('work/keys/train_s1.parquet') k
      JOIN read_parquet('work/v2_candidate_research.parquet') r USING(entity_id)""")
    if kind=='acronym':
        src="SELECT s1,cc,ac ky,nm,addr FROM sources WHERE length(ac) BETWEEN 3 AND 12"
        tar="SELECT mid,target_source,cc,ac ky,nm,addr FROM targets WHERE length(ac) BETWEEN 3 AND 12"
        bit=32
    elif kind=='postal_like_numeric':
        src="SELECT s1,cc,unnest(string_split(nums,',')) ky,nm,addr FROM sources WHERE nums<>''"
        tar="SELECT mid,target_source,cc,unnest(string_split(nums,',')) ky,nm,addr FROM targets WHERE nums<>''"
        bit=64
    else:
        raise ValueError('Only independently preflighted exact-key passes supported')
    condition="length(ky) BETWEEN 4 AND 6" if kind=='postal_like_numeric' else 'true'
    df=case['target_df_path']
    c.execute(f"""CREATE TEMP TABLE selected AS WITH eligible AS (
      SELECT s.*,d.df,row_number() OVER(PARTITION BY s1 ORDER BY d.df,ky) rn
      FROM ({src}) s JOIN read_parquet('{df}') d USING(cc,ky)
      WHERE {condition} AND d.df<={CAP})
      SELECT s1,cc,ky,nm,addr,df FROM eligible WHERE rn=1""")
    est=c.sql("SELECT coalesce(sum(df),0),count(*) FROM selected").fetchone()
    if est[0]!=option['estimated_join_rows'] or est[0]>MAX_JOIN_ROWS:
        raise RuntimeError(f'Exact-key join estimate mismatch: {est}')
    print(kind,'join estimate',est,flush=True)
    raw=R/f'{kind}_raw_hits.parquet';raw_receipt=R/f'{kind}_raw_receipt.json'
    if raw_receipt.exists():
        receipt=json.loads(raw_receipt.read_text())
        if not raw.exists() or sha(raw)!=receipt['sha256']:
            raise RuntimeError('Raw hit receipt mismatch')
    else:
        pending=raw.with_suffix('.pending.parquet')
        if pending.exists(): pending.unlink()
        c.execute(f"""COPY (SELECT s.s1,t.mid,t.target_source,s.cc,
          jaro_winkler_similarity(s.nm,t.nm)::FLOAT name_similarity,
          CASE WHEN s.addr<>'' AND t.addr<>''
            THEN jaro_winkler_similarity(s.addr,t.addr)::FLOAT ELSE 0.0::FLOAT END address_similarity,
          s.df key_df
          FROM selected s JOIN ({tar}) t USING(cc,ky)
          WHERE {condition})
          TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        os.replace(pending,raw)
        count=c.sql(f"SELECT count(*) FROM read_parquet('{raw.as_posix()}')").fetchone()[0]
        if count>MAX_JOIN_ROWS:
            raise RuntimeError('Actual intermediate rows exceed hard cap')
        write_json(raw_receipt,{'status':'CHECKSUMMED_LABEL_FREE_INTERMEDIATE',
            'path':raw.relative_to(ROOT).as_posix(),'sha256':sha(raw),'rows':count,
            'preflight_estimated_join_rows':est[0]})
        print(kind,'raw hits',count,flush=True)
    directory=R/f'{kind}_candidates';directory.mkdir(exist_ok=True)
    parts=[]
    for shard in SHARDS:
        out=directory/f'{shard}.parquet';rp=directory/f'{shard}.json'
        if rp.exists():
            receipt=json.loads(rp.read_text())
            if not out.exists() or sha(out)!=receipt['sha256']:
                raise RuntimeError(f'Candidate shard receipt mismatch: {shard}')
            parts.append(receipt);continue
        if out.exists():raise RuntimeError(f'Unreceipted shard: {out}')
        pending=directory/f'{shard}.pending.parquet'
        if pending.exists():pending.unlink()
        c.execute(f"""COPY (WITH scored AS (
          SELECT s1,mid,target_source,max(name_similarity) name_similarity,
            max(address_similarity) address_similarity,min(key_df) key_df
          FROM read_parquet('{raw.as_posix()}')
          WHERE substr(md5(s1),1,1)='{shard}' GROUP BY 1,2,3), ranked AS (
          SELECT *,row_number() OVER(PARTITION BY s1,target_source
            ORDER BY name_similarity DESC,address_similarity DESC,key_df,mid) source_rank
          FROM scored)
          SELECT s1 source1_entity_id,mid target_entity_id,target_source,
            name_similarity,address_similarity,key_df,source_rank::USMALLINT source_rank,
            {bit}::UTINYINT provenance
          FROM ranked WHERE source_rank<={QUOTA}
          ORDER BY source1_entity_id,target_entity_id)
          TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        os.replace(pending,out)
        n=c.sql(f"SELECT count(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0]
        receipt={'shard':shard,'path':out.relative_to(ROOT).as_posix(),'sha256':sha(out),'rows':n}
        write_json(rp,receipt);parts.append(receipt)
        print(kind,'shard',shard,n,flush=True)
    config={'key_kind':kind,'target_df_cap':CAP,'one_rarest_eligible_key_per_s1':True,
      'per_target_source_quota':QUOTA,'hard_intermediate_row_cap':MAX_JOIN_ROWS,
      'country_partitioned':True,'physical_shards':'first hexadecimal md5(S1 ID)',
      'rank':'name Jaro-Winkler similarity, address Jaro-Winkler similarity, target DF, target ID',
      'provenance_bit':bit,'target_corpus':'complete training S2/S3'}
    manifest={'status':'GENERATED_CHECKSUMMED_PENDING_AUDIT','pass':kind,
      'config':config,'policy_config_sha256':hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),
      'preflight_sha256':sha(R/'other_preflight.json'),
      'baseline_manifest_sha256':sha(R/'v1_baseline_manifest.json'),
      'target_rows':case['complete_target_corpus_rows'],'raw_receipt_sha256':sha(raw_receipt),
      'estimated_join_rows':est[0],'parts':parts,'rows':sum(x['rows'] for x in parts),
      'research_s1':len(research),'labels_read':False}
    write_json(R/f'{kind}_manifest.json',manifest)
    log(f'{kind}_materialization_complete',command=f'.venv\\Scripts\\python.exe -B scripts\\v2_exactkey_materialize.py --pass {kind}',
        v2_research_label_read=False,manifest_sha256=sha(R/f'{kind}_manifest.json'))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--pass',dest='kind',required=True,choices=['acronym','postal_like_numeric'])
    args=parser.parse_args()
    with Monitor(R/f'tmp/{args.kind}') as m:run(args.kind)
    write_json(R/f'{args.kind}_resources.json',m.result())
