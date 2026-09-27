"""Materialize predeclared one-hop matcher-seeded expansion grid, without labels."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.runtime import R,log,sha,write_json,Monitor
from v2_phase2_access import research_only
import duckdb

OUT=ROOT/'work/v2_phase2_r1'
THRESHOLDS=((0.61,'061'),(0.80,'080'),(0.95,'095'))
QUOTAS=(5,10,20)


def connection(cap:int):
    temp=OUT/'tmp'/f'expansion_df{cap}'
    temp.mkdir(parents=True,exist_ok=True)
    c=duckdb.connect()
    c.execute("SET threads=1; SET memory_limit='1500MB'; SET preserve_insertion_order=false")
    c.execute('SET temp_directory=?',[temp.as_posix()])
    c.execute("SET max_temp_directory_size='24GB'")
    return c


def run(cap:int):
    os.chdir(ROOT)
    research=research_only()
    if len(research)!=100000:raise PermissionError('Research allocation changed')
    pre=json.loads((OUT/'neighbor_preflight.json').read_text())
    index=OUT/f'neighbor_index_df{cap}'
    manifest=json.loads((index/'manifest.json').read_text())
    if manifest['status']!='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED':
        raise RuntimeError('Neighbor index not audited')
    for part in manifest['parts']:
        if sha(ROOT/part['path'])!=part['sha256']:
            raise RuntimeError('Neighbor index shard changed')
    seeds=ROOT/pre['seeds_path']
    if sha(seeds)!=pre['seeds_sha256']:
        raise RuntimeError('Frozen matcher seed artifact changed')
    c=connection(cap)
    edges=OUT/f'seed_neighbor_edges_df{cap}.parquet'
    edge_receipt=edges.with_suffix('.json')
    if edges.exists():
        if not edge_receipt.exists() or json.loads(edge_receipt.read_text())['sha256']!=sha(edges):
            raise RuntimeError('Unreceipted or changed seed-neighbor edge artifact')
    else:
        pending=edges.with_suffix('.pending.parquet')
        pending.unlink(missing_ok=True)
        idx=(index/'*.parquet').as_posix()
        query=f"""WITH joined AS (
          SELECT s.s1,s.seed_mid,s.score seed_score,n.neighbor_mid,
            n.seed_source,n.neighbor_source,n.direction,
            n.shared_grams,n.evidence
          FROM read_parquet('{seeds.as_posix()}') s
          JOIN read_parquet('{idx}') n USING(seed_mid)),
          ranked AS (SELECT *,row_number() OVER(PARTITION BY s1,seed_mid
            ORDER BY (seed_source<>neighbor_source) DESC,
              shared_grams DESC,evidence DESC,neighbor_mid) seed_quota_rank
            FROM joined)
          SELECT s1,seed_mid,seed_score,neighbor_mid,seed_source,neighbor_source,
            direction,shared_grams,evidence,seed_quota_rank::UTINYINT seed_quota_rank
          FROM ranked WHERE seed_quota_rank<=20 ORDER BY s1,seed_mid,seed_quota_rank"""
        c.execute(f"COPY ({query}) TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)")
        os.replace(pending,edges)
        row=c.sql(f"""SELECT count(*),
          count(*)-count(DISTINCT(s1,seed_mid,neighbor_mid)),
          max(seed_quota_rank) FROM read_parquet('{edges.as_posix()}')""").fetchone()
        if row[1] or row[2]>20:raise RuntimeError('Seed-neighbor edge audit failed')
        write_json(edge_receipt,{'status':'GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED',
          'population':'v2_candidate_research','path':edges.relative_to(ROOT).as_posix(),
          'sha256':sha(edges),'rows':row[0],'candidate_count':row[0],
          'duplicate_count':row[1],'invalid_id_count':0,'max_seed_quota_rank':row[2],
          'provenance_metadata':{'seed_source':'frozen V1 matcher score',
            'direction':'source namespace for seed and neighbor'},
          'configuration':{'df_cap':cap,'max_per_seed_quota':20,
            'index_manifest_sha256':sha(index/'manifest.json')},'labels_read':False})
    config_dir=OUT/'expansion_grid'
    config_dir.mkdir(exist_ok=True)
    receipts=[]
    for threshold,tag in THRESHOLDS:
        for quota in QUOTAS:
            name=f'matcher_seed_t{tag}_q{quota}_df{cap}'
            path=config_dir/f'{name}.parquet'
            receipt=config_dir/f'{name}.json'
            if path.exists():
                if not receipt.exists() or json.loads(receipt.read_text())['sha256']!=sha(path):
                    raise RuntimeError(f'Unreceipted or changed grid artifact: {path}')
                receipts.append(json.loads(receipt.read_text()))
                continue
            pending=path.with_suffix('.pending.parquet')
            pending.unlink(missing_ok=True)
            query=f"""WITH e AS (SELECT * FROM read_parquet('{edges.as_posix()}')
                WHERE seed_score>={threshold} AND seed_quota_rank<={quota}),
              ranked AS (SELECT *,row_number() OVER(
                  PARTITION BY s1,neighbor_mid ORDER BY seed_score DESC,
                  shared_grams DESC,evidence DESC,seed_mid) chosen_rank,
                  count(*) OVER(PARTITION BY s1,neighbor_mid) supporting_seed_edges
                FROM e)
              SELECT s1 source1_entity_id,neighbor_mid target_entity_id,
                seed_mid,seed_score,direction,seed_source,neighbor_source,
                shared_grams,evidence,supporting_seed_edges::USMALLINT supporting_seed_edges
              FROM ranked WHERE chosen_rank=1 ORDER BY 1,2"""
            print('expansion grid',name,flush=True)
            c.execute(f"COPY ({query}) TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)")
            os.replace(pending,path)
            row=c.sql(f"""SELECT count(*),
                count(*)-count(DISTINCT(x.source1_entity_id,x.target_entity_id)),
                count(*) FILTER(WHERE r.entity_id IS NULL),
                count(*) FILTER(WHERE t.entity_id IS NULL),
                count(*) FILTER(WHERE x.seed_mid=x.target_entity_id)
              FROM read_parquet('{path.as_posix()}') x
              LEFT JOIN read_parquet('work/v2_candidate_research.parquet') r
                ON x.source1_entity_id=r.entity_id
              LEFT JOIN (SELECT entity_id FROM read_parquet('work/keys/train_s2.parquet')
                UNION ALL SELECT entity_id FROM read_parquet('work/keys/train_s3.parquet')) t
                ON x.target_entity_id=t.entity_id""").fetchone()
            if any(row[1:]):raise RuntimeError(f'Expansion grid audit failed: {name}: {row}')
            record={'status':'GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED',
              'population':'v2_candidate_research','path':path.relative_to(ROOT).as_posix(),
              'sha256':sha(path),'rows':row[0],'candidate_count':row[0],
              'duplicate_count':row[1],'invalid_id_count':row[2]+row[3]+row[4],
              'provenance_metadata':{'seed_mid':'frozen V1 matcher accepted target',
                 'direction':'S2/S3 namespace pair','supporting_seed_edges':'deduplicated pair support count'},
              'configuration':{'hop':1,'seed_threshold':threshold,'per_seed_quota':quota,
                'df_cap':cap,'source_index_sha256':sha(index/'manifest.json'),
                'seed_edges_sha256':sha(edges)},'labels_read':False}
            write_json(receipt,record)
            receipts.append(record)
    grid={'status':'GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED',
      'population':'v2_candidate_research','df_cap':cap,'configurations':receipts,
      'seed_edges_receipt_sha256':sha(edge_receipt),'labels_read':False}
    write_json(config_dir/f'df{cap}_manifest.json',grid)
    log('phase2_expansion_grid_complete',population='v2_candidate_research',
      v2_research_label_read=False,df_cap=cap,manifest_sha256=sha(config_dir/f'df{cap}_manifest.json'))
    print(json.dumps({'df_cap':cap,'configurations':[(x['configuration'],x['rows']) for x in receipts]},indent=2))
    c.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--df-cap',type=int,choices=[1000,5000],required=True)
    cap=parser.parse_args().df_cap
    with Monitor(OUT/'tmp'/f'expansion_df{cap}') as monitor:
        run(cap)
    write_json(OUT/f'expansion_grid_df{cap}_resources.json',monitor.result())
