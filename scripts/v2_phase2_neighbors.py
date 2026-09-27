"""Bounded matcher-seed target-to-target index against complete training targets."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.ngram import duckdb_grams_sql
from er.candidates_v2.runtime import R,log,sha,write_json,Monitor
from v2_phase2_access import research_only
import duckdb

OUT=ROOT/'work/v2_phase2_r1'
SHARDS='0123456789abcdef'
MAX_JOIN_ROWS=600_000_000


def connection(cap:int):
    temp=OUT/'tmp'/f'neighbors_df{cap}'
    temp.mkdir(parents=True,exist_ok=True)
    c=duckdb.connect()
    c.execute("SET threads=1; SET memory_limit='1500MB'; SET preserve_insertion_order=false")
    c.execute('SET temp_directory=?',[temp.as_posix()])
    c.execute("SET max_temp_directory_size='24GB'")
    return c


def run(cap:int):
    os.chdir(ROOT)
    research=research_only()
    if len(research)!=100000:raise PermissionError('Population allocation changed')
    pre=json.loads((OUT/'neighbor_preflight.json').read_text())
    if pre['status']!='LABEL_FREE_COMPLETE_TARGET_NEIGHBOR_PREFLIGHT':
        raise RuntimeError('Neighbor preflight missing')
    option=pre['caps'][str(cap)]
    if not option['under_hard_join_cap'] or not option['under_temp_disk_cap']:
        raise RuntimeError(f'DF {cap} rejected by label-free resource preflight')
    keyfile=ROOT/option['selected_keys_path']
    if sha(keyfile)!=option['selected_keys_sha256']:
        raise RuntimeError('Selected seed-target keys changed')
    name=f'neighbor_index_df{cap}'
    dest=OUT/name
    dest.mkdir(exist_ok=True)
    c=connection(cap)
    c.execute("""CREATE TEMP VIEW targets AS
      SELECT entity_id mid,'S2' target_source,country_norm cc,name_norm nm
      FROM read_parquet('work/keys/train_s2.parquet')
      UNION ALL SELECT entity_id,'S3',country_norm,name_norm
      FROM read_parquet('work/keys/train_s3.parquet')""")
    posting=OUT/f'neighbor_postings_df{cap}.parquet'
    posting_receipt=posting.with_suffix('.json')
    if posting.exists():
        if not posting_receipt.exists() or json.loads(posting_receipt.read_text())['sha256']!=sha(posting):
            raise RuntimeError('Unreceipted or changed complete-target posting index')
    else:
        pending=posting.with_suffix('.pending.parquet')
        pending.unlink(missing_ok=True)
        gram=duckdb_grams_sql('nm',4)
        c.execute(f"""COPY (WITH grams AS (
          SELECT mid,target_source,cc,nm,{gram} gram FROM targets
          WHERE length(nm) BETWEEN 4 AND 64)
          SELECT g.* FROM grams g
          JOIN (SELECT DISTINCT cc,gram FROM read_parquet('{keyfile.as_posix()}')) k
            USING(cc,gram))
          TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        os.replace(pending,posting)
        n=c.sql(f"SELECT count(*) FROM read_parquet('{posting.as_posix()}')").fetchone()[0]
        write_json(posting_receipt,{'status':'COMPLETE','population':'complete training S2+S3 targets',
          'path':posting.relative_to(ROOT).as_posix(),'sha256':sha(posting),'rows':n,
          'duplicate_count':0,'invalid_id_count':0,
          'configuration':{'df_cap':cap,'name_chargrams':4,'selected_seed_keys_sha256':sha(keyfile)},
          'target_corpus_rows':10320219,'labels_read':False})
        print('full-target posting rows',n,flush=True)
    postings_sha=sha(posting)
    receipts=[]
    for shard in SHARDS:
        output=dest/f'{shard}.parquet'
        receipt=dest/f'{shard}.json'
        if output.exists():
            if not receipt.exists():raise RuntimeError(f'Unreceipted neighbor shard: {output}')
            old=json.loads(receipt.read_text())
            if old['sha256']!=sha(output) or old['postings_sha256']!=postings_sha:
                raise RuntimeError(f'Neighbor shard changed: {output}')
            receipts.append(old)
            continue
        pending=dest/f'{shard}.pending.parquet'
        pending.unlink(missing_ok=True)
        # K=20 for each target namespace, 40 maximum neighbours per seed.
        # This is a bounded target-to-target graph with S2/S3 provenance.
        query=f"""WITH joined AS (
          SELECT k.seed_mid,p.mid neighbor_mid,p.target_source neighbor_source,
            count(*)::USMALLINT shared_grams,
            sum(ln(1.0+10320219.0/k.df)) evidence
          FROM read_parquet('{keyfile.as_posix()}') k
          JOIN read_parquet('{posting.as_posix()}') p USING(cc,gram)
          WHERE substr(md5(k.seed_mid),1,1)='{shard}' AND p.mid<>k.seed_mid
          GROUP BY 1,2,3), ranked AS (
          SELECT j.*,substr(seed_mid,1,2) seed_source,
            row_number() OVER(PARTITION BY seed_mid,neighbor_source
              ORDER BY shared_grams DESC,evidence DESC,neighbor_mid) neighbor_source_rank
          FROM joined j)
          SELECT seed_mid,neighbor_mid,seed_source,neighbor_source,
            seed_source||'→'||neighbor_source direction,
            shared_grams,evidence::FLOAT evidence,
            neighbor_source_rank::UTINYINT neighbor_source_rank
          FROM ranked WHERE neighbor_source_rank<=20 ORDER BY seed_mid,neighbor_mid"""
        print('neighbor index',cap,shard,flush=True)
        c.execute(f"COPY ({query}) TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)")
        os.replace(pending,output)
        row=c.sql(f"""SELECT count(*) n,
          count(*)-count(DISTINCT(seed_mid,neighbor_mid)) duplicate_edges,
          count(*) FILTER(WHERE seed_mid=neighbor_mid) self_edges,
          max(neighbor_source_rank) max_source_rank,
          count(*) FILTER(WHERE seed_source<>neighbor_source) cross_source,
          count(*) FILTER(WHERE seed_source=neighbor_source) same_source
          FROM read_parquet('{output.as_posix()}')""").fetchone()
        if row[1] or row[2] or row[3]>20:raise RuntimeError(f'Neighbor shard failed structural audit: {output}: {row}')
        record={'path':output.relative_to(ROOT).as_posix(),'sha256':sha(output),
          'rows':row[0],'candidate_count':row[0],'duplicate_count':row[1],
          'invalid_id_count':row[2],'max_per_target_source_rank':row[3],
          'cross_source':row[4],'same_source':row[5],
          'population':'complete training S2+S3 target corpus',
          'postings_sha256':postings_sha,'configuration':{'df_cap':cap,'shard':shard,
            'per_seed_target_K_per_source':20},'labels_read':False}
        write_json(receipt,record)
        receipts.append(record)
    manifest={'status':'GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED',
      'population':'complete training S2+S3 target corpus',
      'research_seed_population':'v2_candidate_research',
      'complete_target_rows':10320219,'df_cap':cap,
      'posting_receipt_sha256':sha(posting_receipt),
      'selected_keys_sha256':sha(keyfile),
      'estimated_join_rows':option['estimated_join_rows'],
      'rows':sum(x['rows'] for x in receipts),
      'candidate_count':sum(x['rows'] for x in receipts),
      'duplicate_count':sum(x['duplicate_count'] for x in receipts),
      'invalid_id_count':sum(x['invalid_id_count'] for x in receipts),
      'cross_source':sum(x['cross_source'] for x in receipts),
      'same_source':sum(x['same_source'] for x in receipts),
      'configuration':{'name_chargram_n':4,'rare_keys_per_seed_target':4,
        'per_seed_target_K_per_source':20,'same_country':True,'one_hop':True,
        'tie_break':'shared_grams DESC,evidence DESC,neighbor_mid ASC'},
      'parts':receipts,'labels_read':False}
    write_json(dest/'manifest.json',manifest)
    log('phase2_neighbor_index_complete',population='v2_candidate_research',
      v2_research_label_read=False,df_cap=cap,manifest_sha256=sha(dest/'manifest.json'))
    print(json.dumps({k:v for k,v in manifest.items() if k!='parts'},indent=2))
    c.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--df-cap',type=int,choices=[1000,5000],required=True)
    cap=parser.parse_args().df_cap
    with Monitor(OUT/'tmp'/f'neighbors_df{cap}') as monitor:
        run(cap)
    write_json(OUT/f'neighbor_index_df{cap}_resources.json',monitor.result())
