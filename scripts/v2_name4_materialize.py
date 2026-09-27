"""Materialize a bounded, label-free name four-gram retrieval pass.

Research labels are neither imported nor read here. The complete S2/S3 target
corpus is scanned to construct country-partitioned postings. Physical shards
use stable hexadecimal MD5 prefixes of S1 IDs and have atomic receipts.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.ngram import duckdb_grams_sql
from er.candidates_v2.runtime import R,W,sha,write_json,log,connect,populations,Monitor

PASS='name_char4'
CAP=1000
TOP_GRAMS=4
PER_SOURCE_QUOTA=25
MAX_JOIN_ROWS=60_000_000
PARTS='0123456789abcdef'


def verified_preflight():
    proof=json.loads((R/'ngram_preflight.json').read_text())
    baseline=json.loads((R/'v1_baseline_manifest.json').read_text())
    if proof['status']!='FULL_TARGET_DF_PREFLIGHT' or baseline['status']!='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED':
        raise PermissionError('Complete-corpus DF preflight and audited baseline required')
    item=next(x for x in proof['results'] if x['n']==4)
    if sha(ROOT/item['df_path'])!=item['df_sha256']:
        raise RuntimeError('DF file checksum differs')
    option=next(x for x in item['bounded_options'] if x['df_cap']==CAP and x['top_grams_per_s1']==TOP_GRAMS)
    if option['estimated_join_rows']>MAX_JOIN_ROWS:
        raise RuntimeError('Four-gram join estimate exceeds hard cap')
    for name in ('train_s2','train_s3'):
        if sha(W/'keys'/f'{name}.parquet')!=baseline['inputs'][name]['sha256']:
            raise RuntimeError('Complete target corpus changed')
    return proof,item,option,baseline


def run():
    os.chdir(ROOT)
    research,sealed=populations()
    proof,item,option,baseline=verified_preflight()
    directory=R/f'{PASS}_candidates'; directory.mkdir(exist_ok=True)
    c=connect(PASS)
    expr=duckdb_grams_sql('nm',4)
    c.execute("""CREATE TEMP VIEW sources AS SELECT k.entity_id s1,k.country_norm cc,k.name_norm nm
      FROM read_parquet('work/keys/train_s1.parquet') k
      JOIN read_parquet('work/v2_candidate_research.parquet') r USING(entity_id)""")
    c.execute("""CREATE TEMP VIEW targets AS SELECT entity_id mid,'s2' target_source,country_norm cc,name_norm nm
      FROM read_parquet('work/keys/train_s2.parquet') UNION ALL
      SELECT entity_id,'s3',country_norm,name_norm FROM read_parquet('work/keys/train_s3.parquet')""")
    c.execute(f"""CREATE TEMP TABLE selected AS WITH eligible AS (
      SELECT s1,s.cc,s.gram,d.df,
        row_number() OVER(PARTITION BY s1 ORDER BY d.df,s.gram) key_rank
      FROM (SELECT s1,cc,{expr} gram FROM sources WHERE length(nm) BETWEEN 4 AND 64) s
      JOIN read_parquet('{item['df_path']}') d ON s.cc=d.cc AND s.gram=d.gram
      WHERE d.df<={CAP}) SELECT * FROM eligible WHERE key_rank<={TOP_GRAMS}""")
    estimated=c.sql("""WITH x AS (SELECT cc,gram,count(*) source_n,max(df) target_df FROM selected GROUP BY 1,2)
      SELECT coalesce(sum(source_n*target_df),0),coalesce(max(source_n*target_df),0),count(*) FROM x""").fetchone()
    if estimated[0]>MAX_JOIN_ROWS or estimated[0]!=option['estimated_join_rows']:
        raise RuntimeError(f'Join guard/preflight mismatch: {estimated}')
    print('selected gram join estimate',estimated,flush=True)
    postings=R/f'{PASS}_postings.parquet'; pending=postings.with_suffix('.pending.parquet')
    posting_receipt=R/f'{PASS}_postings_receipt.json'
    if posting_receipt.exists():
        receipt=json.loads(posting_receipt.read_text())
        if not postings.exists() or sha(postings)!=receipt['sha256']:
            raise RuntimeError('Existing posting receipt does not match')
    else:
        if pending.exists(): pending.unlink()
        c.execute(f"""COPY (SELECT g.mid,g.target_source,g.cc,g.gram FROM
          (SELECT mid,target_source,cc,{expr} gram FROM targets WHERE length(nm) BETWEEN 4 AND 64) g
          JOIN (SELECT DISTINCT cc,gram FROM selected) k USING(cc,gram))
          TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        os.replace(pending,postings)
        count=c.sql(f"SELECT count(*) FROM read_parquet('{postings.as_posix()}')").fetchone()[0]
        write_json(posting_receipt,{'status':'COMPLETE_TARGET_POSTINGS','path':postings.relative_to(ROOT).as_posix(),
            'sha256':sha(postings),'rows':count,'target_rows':proof['target_rows'],
            'country_partitioned':True,'selected_gram_join_bound':estimated[0]})
        print('wrote target postings',count,flush=True)
    post=postings.as_posix()
    parts=[]
    for shard in PARTS:
        out=directory/f'{shard}.parquet'; receipt_path=directory/f'{shard}.json'
        if receipt_path.exists():
            receipt=json.loads(receipt_path.read_text())
            if not out.exists() or sha(out)!=receipt['sha256']:
                raise RuntimeError(f'Existing shard receipt mismatch: {shard}')
            parts.append(receipt); continue
        if out.exists(): raise RuntimeError(f'Unreceipted shard: {out}')
        pending=directory/f'{shard}.pending.parquet'
        if pending.exists(): pending.unlink()
        # A country match is mandatory. Each source contributes at most 25
        # candidate identities per S1. Scores use only target DF and grams.
        c.execute(f"""COPY (WITH hits AS (
          SELECT s.s1,p.mid,p.target_source,count(*)::UTINYINT shared_grams,
            sum(ln(1.0+{proof['target_rows']}::DOUBLE/s.df)) evidence
          FROM selected s JOIN read_parquet('{post}') p
            ON s.cc=p.cc AND s.gram=p.gram
          WHERE substr(md5(s.s1),1,1)='{shard}'
          GROUP BY 1,2,3), ranked AS (
          SELECT *,row_number() OVER(PARTITION BY s1,target_source
            ORDER BY evidence DESC,shared_grams DESC,mid) source_rank FROM hits)
          SELECT s1 source1_entity_id,mid target_entity_id,target_source,
            shared_grams,evidence::FLOAT name4_evidence,source_rank::USMALLINT source_rank,
            16::UTINYINT provenance
          FROM ranked WHERE source_rank<={PER_SOURCE_QUOTA}
          ORDER BY source1_entity_id,target_entity_id)
          TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        os.replace(pending,out)
        row=c.sql(f"SELECT count(*),count(DISTINCT source1_entity_id) FROM read_parquet('{out.as_posix()}')").fetchone()
        receipt={'shard':shard,'path':out.relative_to(ROOT).as_posix(),'sha256':sha(out),
                 'rows':row[0],'s1_with_candidates':row[1]}
        write_json(receipt_path,receipt);parts.append(receipt)
        print('completed four-gram shard',shard,row,flush=True)
    result={'status':'GENERATED_CHECKSUMMED_PENDING_AUDIT','pass':PASS,
      'config':{'n':4,'target_df_cap':CAP,'top_grams_per_s1':TOP_GRAMS,
                'source_quota_each':PER_SOURCE_QUOTA,'max_intermediate_join_rows':MAX_JOIN_ROWS,
                'name_length_min':4,'name_length_max':64,'country_partitioned':True,
                'physical_shards':'first hexadecimal md5(S1 ID)','provenance_bit':16,
                'scoring':'sum log(1+complete_target_rows/country_target_DF), descending; then shared grams, target ID'},
      'policy_config_sha256':None,'preflight_sha256':sha(R/'ngram_preflight.json'),
      'complete_target_rows':proof['target_rows'],'target_s2_sha256':baseline['inputs']['train_s2']['sha256'],
      'target_s3_sha256':baseline['inputs']['train_s3']['sha256'],
      'posting_receipt_sha256':sha(posting_receipt),'estimated_join_rows':estimated[0],
      'worst_key_join_rows':estimated[1],'parts':parts,'rows':sum(p['rows'] for p in parts),
      'research_s1':len(research),'labels_read':False}
    import hashlib
    result['policy_config_sha256']=hashlib.sha256(json.dumps(result['config'],sort_keys=True).encode()).hexdigest()
    write_json(R/f'{PASS}_manifest.json',result)
    log('name_char4_materialization_complete',command='.venv\\Scripts\\python.exe -B scripts\\v2_name4_materialize.py',
        v2_research_label_read=False,manifest_sha256=sha(R/f'{PASS}_manifest.json'))


if __name__=='__main__':
    with Monitor(R/'tmp/name_char4') as m: run()
    write_json(R/'name_char4_resources.json',m.result())
