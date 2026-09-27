"""One-stage, one-seed, opposite-source, label-free sister expansion."""
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

PASS='sister_expansion'
DF_CAP=1000
TOP_GRAMS=4
QUOTA=10
MAX_JOIN_ROWS=60_000_000
SHARDS='0123456789abcdef'


def run():
    os.chdir(ROOT)
    research,sealed=populations()
    seed_proof=json.loads((R/'sister_seed_key_preflight.json').read_text())
    ngram=json.loads((R/'ngram_preflight.json').read_text())
    df=next(x for x in ngram['results'] if x['n']==4)
    if seed_proof['status']!='LABEL_FREE_STRICT_ONE_SEED_PREFLIGHT' or \
       sha(ROOT/seed_proof['seed_file'])!=seed_proof['seed_file_sha256'] or \
       sha(ROOT/df['df_path'])!=df['df_sha256']:
        raise PermissionError('Seed/DF preflight changed')
    if seed_proof['estimated_same_country_all_source_join_rows_upper_bound']>MAX_JOIN_ROWS:
        raise RuntimeError('Seed join estimate exceeds hard cap')
    c=connect(PASS)
    c.execute("""CREATE TEMP VIEW targets AS SELECT entity_id mid,'s2' target_source,
      country_norm cc,name_norm nm FROM read_parquet('work/keys/train_s2.parquet')
      UNION ALL SELECT entity_id,'s3',country_norm,name_norm
      FROM read_parquet('work/keys/train_s3.parquet')""")
    expr=duckdb_grams_sql('nm',4)
    c.execute(f"""CREATE TEMP TABLE selected AS WITH seed_grams AS (
      SELECT s.s1,s.seed_mid,t.target_source seed_source,t.cc,t.nm seed_name,{expr} gram
      FROM read_parquet('{seed_proof['seed_file']}') s JOIN targets t ON s.seed_mid=t.mid
      WHERE length(t.nm) BETWEEN 4 AND 64), eligible AS (
      SELECT g.*,d.df,row_number() OVER(PARTITION BY s1 ORDER BY d.df,g.gram) key_rank
      FROM seed_grams g JOIN read_parquet('{df['df_path']}') d
      ON g.cc=d.cc AND g.gram=d.gram WHERE d.df<={DF_CAP})
      SELECT * FROM eligible WHERE key_rank<={TOP_GRAMS}""")
    est=c.sql("""WITH x AS (SELECT cc,gram,count(*) sn,max(df) dn FROM selected GROUP BY 1,2)
      SELECT coalesce(sum(sn*dn),0),coalesce(max(sn*dn),0) FROM x""").fetchone()
    if est[0]!=seed_proof['estimated_same_country_all_source_join_rows_upper_bound'] or est[0]>MAX_JOIN_ROWS:
        raise RuntimeError(f'Sister join guard/preflight mismatch: {est}')
    print('sister upper-bound joins',est,flush=True)
    posting=R/f'{PASS}_postings.parquet';receipt_path=R/f'{PASS}_postings_receipt.json'
    if receipt_path.exists():
        receipt=json.loads(receipt_path.read_text())
        if not posting.exists() or sha(posting)!=receipt['sha256']:
            raise RuntimeError('Sister posting receipt mismatch')
    else:
        pending=posting.with_suffix('.pending.parquet')
        if pending.exists():pending.unlink()
        c.execute(f"""COPY (SELECT g.mid,g.target_source,g.cc,g.nm target_name,g.gram FROM
          (SELECT mid,target_source,cc,nm,{expr} gram FROM targets WHERE length(nm) BETWEEN 4 AND 64) g
          JOIN (SELECT DISTINCT cc,gram FROM selected) k USING(cc,gram))
          TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        os.replace(pending,posting)
        n=c.sql(f"SELECT count(*) FROM read_parquet('{posting.as_posix()}')").fetchone()[0]
        write_json(receipt_path,{'status':'COMPLETE_TARGET_SISTER_POSTINGS',
          'path':posting.relative_to(ROOT).as_posix(),'sha256':sha(posting),'rows':n,
          'full_target_rows':ngram['target_rows']})
        print('sister target postings',n,flush=True)
    directory=R/f'{PASS}_candidates';directory.mkdir(exist_ok=True)
    parts=[]
    for shard in SHARDS:
        out=directory/f'{shard}.parquet';rp=directory/f'{shard}.json'
        if rp.exists():
            receipt=json.loads(rp.read_text())
            if not out.exists() or sha(out)!=receipt['sha256']:
                raise RuntimeError(f'Sister shard receipt mismatch: {shard}')
            parts.append(receipt);continue
        if out.exists():raise RuntimeError(f'Unreceipted sister shard: {out}')
        pending=directory/f'{shard}.pending.parquet'
        if pending.exists():pending.unlink()
        c.execute(f"""COPY (WITH hits AS (
          SELECT s.s1,p.mid,p.target_source,count(*)::UTINYINT shared_grams,
            sum(ln(1.0+{ngram['target_rows']}::DOUBLE/s.df)) evidence,
            any_value(s.seed_name) seed_name,any_value(p.target_name) target_name
          FROM selected s JOIN read_parquet('{posting.as_posix()}') p
            ON s.cc=p.cc AND s.gram=p.gram
          WHERE substr(md5(s.s1),1,1)='{shard}'
            AND p.target_source<>s.seed_source AND p.mid<>s.seed_mid
          GROUP BY 1,2,3), ranked AS (
          SELECT *,row_number() OVER(PARTITION BY s1 ORDER BY evidence DESC,
            shared_grams DESC,jaro_winkler_similarity(seed_name,target_name) DESC,mid) source_rank
          FROM hits)
          SELECT s1 source1_entity_id,mid target_entity_id,target_source,
            shared_grams,evidence::FLOAT sister_name_evidence,
            source_rank::USMALLINT source_rank,256::USMALLINT provenance
          FROM ranked WHERE source_rank<={QUOTA}
          ORDER BY source1_entity_id,target_entity_id)
          TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        os.replace(pending,out)
        n=c.sql(f"SELECT count(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0]
        receipt={'shard':shard,'path':out.relative_to(ROOT).as_posix(),'sha256':sha(out),'rows':n}
        write_json(rp,receipt);parts.append(receipt)
        print('sister shard',shard,n,flush=True)
    config={'stage_count':1,'max_inference_selected_seeds_per_s1':1,
      'seed_policy':'frozen V1 global top one by source-balanced rank, name IDF, address IDF, target ID',
      'opposite_target_source_only':True,'name_chargram_n':4,'country_target_df_cap':DF_CAP,
      'top_rare_grams_per_seed':TOP_GRAMS,'per_s1_expansion_quota':QUOTA,
      'hard_intermediate_join_cap':MAX_JOIN_ROWS,'country_partitioned':True,
      'physical_shards':'first hexadecimal md5(S1 ID)','provenance_bit':256,
      'rank':'sum log(1+complete_target_rows/country_target_DF), shared grams, seed-target name similarity, target ID'}
    manifest={'status':'GENERATED_CHECKSUMMED_PENDING_AUDIT','pass':PASS,
      'config':config,'policy_config_sha256':hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),
      'seed_preflight_sha256':sha(R/'sister_seed_key_preflight.json'),
      'seed_file_sha256':seed_proof['seed_file_sha256'],
      'target_rows':ngram['target_rows'],'posting_receipt_sha256':sha(receipt_path),
      'estimated_join_rows_upper_bound':est[0],'worst_key_join_rows':est[1],
      'parts':parts,'rows':sum(x['rows'] for x in parts),'research_s1':len(research),
      'labels_read':False}
    write_json(R/f'{PASS}_manifest.json',manifest)
    log('sister_materialization_complete',command='.venv\\Scripts\\python.exe -B scripts\\v2_sister_materialize.py',
      v2_research_label_read=False,manifest_sha256=sha(R/f'{PASS}_manifest.json'))


if __name__=='__main__':
    with Monitor(R/'tmp/sister_expansion') as m:run()
    write_json(R/'sister_expansion_resources.json',m.result())
