"""Bounded label-free address four-gram retrieval over the complete target corpus."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.ngram import duckdb_grams_sql
from er.candidates_v2.runtime import R,W,sha,write_json,log,connect,populations,Monitor

CAP=200
TOP=4
QUOTA=15
JOIN_CAP=8_000_000
SHARDS='0123456789abcdef'
PASS='address_char4'


def run():
    os.chdir(ROOT)
    research,sealed=populations()
    proof=json.loads((R/'other_preflight.json').read_text())
    case=proof['cases'][PASS]
    if proof['status']!='FULL_TARGET_ALTERNATE_KEY_PREFLIGHT' or sha(ROOT/case['target_df_path'])!=case['target_df_sha256']:
        raise PermissionError('Complete-target address DF preflight changed')
    option=next(x for x in case['options'] if x['df_cap']==CAP and x['max_keys_per_s1']==TOP)
    if option['estimated_join_rows']>JOIN_CAP:
        raise RuntimeError('Estimated address posting join exceeds hard cap')
    c=connect(PASS)
    expr=duckdb_grams_sql('nm',4)
    c.execute("""CREATE TEMP VIEW sources AS SELECT k.entity_id s1,k.country_norm cc,
      k.addr_norm nm,k.name_norm source_name FROM read_parquet('work/keys/train_s1.parquet') k
      JOIN read_parquet('work/v2_candidate_research.parquet') r USING(entity_id)""")
    c.execute("""CREATE TEMP VIEW targets AS SELECT entity_id mid,'s2' target_source,
      country_norm cc,addr_norm nm,name_norm target_name FROM read_parquet('work/keys/train_s2.parquet')
      UNION ALL SELECT entity_id,'s3',country_norm,addr_norm,name_norm
      FROM read_parquet('work/keys/train_s3.parquet')""")
    c.execute(f"""CREATE TEMP TABLE selected AS WITH src AS (
      SELECT s1,cc,source_name,{expr} gram FROM sources WHERE length(nm) BETWEEN 4 AND 96),
      eligible AS (SELECT s1,s.cc,source_name,s.gram,d.df,
        row_number() OVER(PARTITION BY s1 ORDER BY d.df,s.gram) key_rank
        FROM src s JOIN read_parquet('{case['target_df_path']}') d
        ON s.cc=d.cc AND s.gram=d.ky WHERE d.df<={CAP})
      SELECT * FROM eligible WHERE key_rank<={TOP}""")
    estimate=c.sql("""WITH x AS (SELECT cc,gram,count(*) sn,max(df) dn FROM selected GROUP BY 1,2)
      SELECT coalesce(sum(sn*dn),0),coalesce(max(sn*dn),0),count(*) FROM x""").fetchone()
    if estimate[0]!=option['estimated_join_rows'] or estimate[0]>JOIN_CAP:
        raise RuntimeError(f'Address join guard/preflight mismatch: {estimate}')
    print('address4 estimated join rows',estimate,flush=True)
    posting=R/f'{PASS}_postings.parquet';receipt_path=R/f'{PASS}_postings_receipt.json'
    if receipt_path.exists():
        receipt=json.loads(receipt_path.read_text())
        if not posting.exists() or sha(posting)!=receipt['sha256']:
            raise RuntimeError('Address posting receipt mismatch')
    else:
        pending=posting.with_suffix('.pending.parquet')
        if pending.exists():pending.unlink()
        c.execute(f"""COPY (SELECT g.mid,g.target_source,g.cc,g.target_name,g.gram FROM
          (SELECT mid,target_source,cc,target_name,{expr} gram FROM targets
           WHERE length(nm) BETWEEN 4 AND 96) g
          JOIN (SELECT DISTINCT cc,gram FROM selected) k USING(cc,gram))
          TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        os.replace(pending,posting)
        n=c.sql(f"SELECT count(*) FROM read_parquet('{posting.as_posix()}')").fetchone()[0]
        write_json(receipt_path,{'status':'COMPLETE_TARGET_ADDRESS_POSTINGS',
          'path':posting.relative_to(ROOT).as_posix(),'sha256':sha(posting),'rows':n,
          'target_corpus_rows':case['complete_target_corpus_rows']})
        print('address4 target postings',n,flush=True)
    directory=R/f'{PASS}_candidates';directory.mkdir(exist_ok=True)
    parts=[]
    for shard in SHARDS:
        out=directory/f'{shard}.parquet';rp=directory/f'{shard}.json'
        if rp.exists():
            receipt=json.loads(rp.read_text())
            if not out.exists() or sha(out)!=receipt['sha256']:
                raise RuntimeError(f'Address candidate shard receipt mismatch: {shard}')
            parts.append(receipt);continue
        if out.exists():raise RuntimeError(f'Unreceipted address shard: {out}')
        pending=directory/f'{shard}.pending.parquet'
        if pending.exists():pending.unlink()
        c.execute(f"""COPY (WITH hits AS (
          SELECT s.s1,p.mid,p.target_source,count(*)::UTINYINT shared_grams,
            sum(ln(1.0+{case['complete_target_corpus_rows']}::DOUBLE/s.df)) address_evidence,
            any_value(s.source_name) source_name,any_value(p.target_name) target_name
          FROM selected s JOIN read_parquet('{posting.as_posix()}') p
            ON s.cc=p.cc AND s.gram=p.gram
          WHERE substr(md5(s.s1),1,1)='{shard}' GROUP BY 1,2,3), ranked AS (
          SELECT *,row_number() OVER(PARTITION BY s1,target_source
            ORDER BY address_evidence DESC,shared_grams DESC,
            jaro_winkler_similarity(source_name,target_name) DESC,mid) source_rank FROM hits)
          SELECT s1 source1_entity_id,mid target_entity_id,target_source,
            shared_grams,address_evidence::FLOAT address4_evidence,
            source_rank::USMALLINT source_rank,128::UTINYINT provenance
          FROM ranked WHERE source_rank<={QUOTA}
          ORDER BY source1_entity_id,target_entity_id)
          TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        os.replace(pending,out)
        n=c.sql(f"SELECT count(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0]
        receipt={'shard':shard,'path':out.relative_to(ROOT).as_posix(),'sha256':sha(out),'rows':n}
        write_json(rp,receipt);parts.append(receipt)
        print('address4 shard',shard,n,flush=True)
    config={'n':4,'target_df_cap':CAP,'top_grams_per_s1':TOP,
      'per_target_source_quota':QUOTA,'hard_intermediate_join_cap':JOIN_CAP,
      'address_length_min':4,'address_length_max':96,'country_partitioned':True,
      'physical_shards':'first hexadecimal md5(S1 ID)','provenance_bit':128,
      'rank':'sum log(1+complete_target_rows/country_target_DF), shared grams, name similarity, target ID'}
    manifest={'status':'GENERATED_CHECKSUMMED_PENDING_AUDIT','pass':PASS,
      'config':config,'policy_config_sha256':hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),
      'preflight_sha256':sha(R/'other_preflight.json'),
      'target_rows':case['complete_target_corpus_rows'],'posting_receipt_sha256':sha(receipt_path),
      'estimated_join_rows':estimate[0],'worst_key_join_rows':estimate[1],
      'parts':parts,'rows':sum(x['rows'] for x in parts),'research_s1':len(research),
      'labels_read':False}
    write_json(R/f'{PASS}_manifest.json',manifest)
    log('address_char4_materialization_complete',command='.venv\\Scripts\\python.exe -B scripts\\v2_address4_materialize.py',
      v2_research_label_read=False,manifest_sha256=sha(R/f'{PASS}_manifest.json'))


if __name__=='__main__':
    with Monitor(R/'tmp/address_char4') as m:run()
    write_json(R/'address_char4_resources.json',m.result())
