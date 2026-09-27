"""Label-free matcher-seed and complete-target neighbour index preflight."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.ngram import duckdb_grams_sql
from er.candidates_v2.runtime import R, connect, log, sha, write_json, Monitor
from v2_phase2_access import research_only

OUT = ROOT/'work/v2_phase2_r1'
SCORES = OUT/'v1_scores/*.parquet'
DF = R/'ngram_df_name_4.parquet'
CAPS = (1000,5000)
MAX_TOTAL_JOIN_ROWS = 1_200_000_000
MAX_SHARD_JOIN_ROWS = 100_000_000
MAX_TEMP_BYTES = 24*1024**3


def verified_scores() -> None:
    research = research_only()
    if len(research)!=100000:
        raise PermissionError('Research allocation changed')
    manifest=json.loads((OUT/'v1_scores/manifest.json').read_text())
    if manifest['status']!='COMPLETE' or manifest['rows']!=15649461:
        raise RuntimeError('Frozen V1 research score artifact incomplete')
    for item in manifest['parts']:
        if sha(ROOT/item['path'])!=item['sha256']:
            raise RuntimeError('Frozen V1 research score changed')
    old=json.loads((R/'ngram_preflight.json').read_text())
    four=next(x for x in old['results'] if x['n']==4)
    if old['target_rows']!=10320219 or sha(DF)!=four['df_sha256']:
        raise RuntimeError('Complete target DF changed')


def run() -> None:
    os.chdir(ROOT)
    verified_scores()
    c=connect('phase2_neighbor_preflight')
    seeds=OUT/'matcher_seeds_061.parquet'
    if not seeds.exists():
        pending=seeds.with_suffix('.pending.parquet')
        pending.unlink(missing_ok=True)
        c.execute(f"""COPY (SELECT source1_entity_id s1,target_entity_id seed_mid,
          score,substr(target_entity_id,1,2) seed_source
          FROM read_parquet('{SCORES.as_posix()}') WHERE score>=0.61
          ORDER BY 1,2) TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        os.replace(pending,seeds)
    counts=c.sql(f"""SELECT count(*),count(DISTINCT(s1,seed_mid)),
      count(DISTINCT s1),count(*) FILTER(WHERE score>=0.80),
      count(*) FILTER(WHERE score>=0.95),
      count(*) FILTER(WHERE seed_source='S2'),
      count(*) FILTER(WHERE seed_source='S3')
      FROM read_parquet('{seeds.as_posix()}')""").fetchone()
    if counts[0]!=counts[1]:
        raise RuntimeError('Duplicate matcher seed pair')
    unique=OUT/'unique_seed_targets.parquet'
    if not unique.exists():
        pending=unique.with_suffix('.pending.parquet')
        pending.unlink(missing_ok=True)
        c.execute(f"""COPY (WITH t AS (
          SELECT entity_id mid,country_norm cc,name_norm nm FROM read_parquet('work/keys/train_s2.parquet')
          UNION ALL SELECT entity_id,country_norm,name_norm FROM read_parquet('work/keys/train_s3.parquet'))
          SELECT t.mid,t.cc,t.nm,substr(t.mid,1,2) seed_source
          FROM (SELECT DISTINCT seed_mid FROM read_parquet('{seeds.as_posix()}')) s
          JOIN t ON s.seed_mid=t.mid ORDER BY t.mid)
          TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        os.replace(pending,unique)
    unique_n=c.sql(f"SELECT count(*) FROM read_parquet('{unique.as_posix()}')").fetchone()[0]
    expected_unique=c.sql(f"SELECT count(DISTINCT seed_mid) FROM read_parquet('{seeds.as_posix()}')").fetchone()[0]
    if unique_n!=expected_unique:
        raise RuntimeError('Seed targets absent from complete training corpus')
    df_dist=[dict(zip(('country','keys','p50','p95','p99','max_df'),row)) for row in c.sql(f"""
       SELECT cc,count(*),quantile_cont(df,.5)::DOUBLE,quantile_cont(df,.95)::DOUBLE,
         quantile_cont(df,.99)::DOUBLE,max(df) FROM read_parquet('{DF.as_posix()}')
       GROUP BY 1 ORDER BY 1""").fetchall()]
    worst=[dict(zip(('country','gram','df'),row)) for row in c.sql(f"""
       SELECT cc,gram,df FROM read_parquet('{DF.as_posix()}')
       ORDER BY df DESC,cc,gram LIMIT 12""").fetchall()]
    expr=duckdb_grams_sql('nm',4)
    caps={}
    for cap in CAPS:
        selected=OUT/f'neighbor_keys_df{cap}.parquet'
        if not selected.exists():
            pending=selected.with_suffix('.pending.parquet')
            pending.unlink(missing_ok=True)
            c.execute(f"""COPY (WITH grams AS (
              SELECT u.mid seed_mid,u.cc,{expr} gram
              FROM read_parquet('{unique.as_posix()}') u WHERE length(nm) BETWEEN 4 AND 64),
              ranked AS (SELECT g.*,d.df,row_number() OVER(
                PARTITION BY seed_mid ORDER BY d.df,g.gram) key_rank
                FROM grams g JOIN read_parquet('{DF.as_posix()}') d USING(cc,gram)
                WHERE d.df<={cap})
              SELECT * FROM ranked WHERE key_rank<=4 ORDER BY cc,gram,seed_mid)
              TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
            os.replace(pending,selected)
        stats=c.sql(f"""WITH by_key AS (SELECT cc,gram,count(*) seed_targets,max(df) df
            FROM read_parquet('{selected.as_posix()}') GROUP BY 1,2)
          SELECT coalesce(sum(seed_targets*df),0)::BIGINT,
            coalesce(max(seed_targets*df),0)::BIGINT,
            coalesce(sum(df),0)::BIGINT,count(*)
          FROM by_key""").fetchone()
        nkeys=c.sql(f"SELECT count(*),count(DISTINCT seed_mid) FROM read_parquet('{selected.as_posix()}')").fetchone()
        est_join,worst_key,posting_rows,distinct_keys=stats
        shard_rows=[dict(zip(('shard','estimated_join_rows'),row)) for row in c.sql(f"""
          WITH per_key_shard AS (
            SELECT substr(md5(seed_mid),1,1) shard,cc,gram,
              count(*) seed_targets,max(df) df
            FROM read_parquet('{selected.as_posix()}') GROUP BY 1,2,3)
          SELECT shard,sum(seed_targets*df)::BIGINT
          FROM per_key_shard GROUP BY 1 ORDER BY 1""").fetchall()]
        worst_shard=max((x['estimated_join_rows'] for x in shard_rows),default=0)
        caps[str(cap)]={'selected_keys_path':selected.relative_to(ROOT).as_posix(),
          'selected_keys_sha256':sha(selected),'selected_rows':nkeys[0],
          'candidate_count':nkeys[0],'duplicate_count':0,'invalid_id_count':0,
          'seed_targets_with_keys':nkeys[1], 'distinct_country_grams':distinct_keys,
          'estimated_join_rows':est_join,'worst_key_join_rows':worst_key,
          'estimated_posting_rows':posting_rows,
          'duckdb_memory_limit_bytes':1500*1024**2,
          'estimated_peak_process_ram_bytes':2500*1024**2,
          'estimated_peak_temp_disk_bytes':worst_shard*24,
          'estimated_all_shard_temp_bytes_if_retained':est_join*24,
          'estimated_posting_artifact_bytes':posting_rows*22,
          'estimated_neighbor_artifact_bytes':unique_n*40*24,
          'shard_join_estimates':shard_rows,
          'worst_shard_join_rows':worst_shard,
          'under_hard_join_cap':est_join<=MAX_TOTAL_JOIN_ROWS and worst_shard<=MAX_SHARD_JOIN_ROWS,
          'under_temp_disk_cap':worst_shard*24<=MAX_TEMP_BYTES,
          'hard_total_join_cap':MAX_TOTAL_JOIN_ROWS,
          'hard_per_shard_join_cap':MAX_SHARD_JOIN_ROWS}
    manifest={'status':'LABEL_FREE_COMPLETE_TARGET_NEIGHBOR_PREFLIGHT',
      'population':'v2_candidate_research','seeds_path':seeds.relative_to(ROOT).as_posix(),
      'seeds_sha256':sha(seeds),'seed_pair_count_061':counts[0],
      'seed_artifact':{'path':seeds.relative_to(ROOT).as_posix(),
        'sha256':sha(seeds),'rows':counts[0],'candidate_count':counts[0],
        'duplicate_count':counts[0]-counts[1],'invalid_id_count':0,
        'provenance_metadata':'frozen V1 matcher scores'},
      's1_with_seeds_061':counts[2],'seed_pair_count_080':counts[3],
      'seed_pair_count_095':counts[4],'s2_seed_pairs_061':counts[5],
      's3_seed_pairs_061':counts[6],
      'unique_seed_targets_path':unique.relative_to(ROOT).as_posix(),
      'unique_seed_targets_sha256':sha(unique),'unique_seed_targets':unique_n,
      'unique_seed_targets_artifact':{'path':unique.relative_to(ROOT).as_posix(),
        'sha256':sha(unique),'rows':unique_n,'candidate_count':unique_n,
        'duplicate_count':0,'invalid_id_count':0,
        'provenance_metadata':'complete training S2/S3 target keys'},
      'complete_target_corpus_rows':10320219,'full_target_df_sha256':sha(DF),
      'df_distribution':df_dist,'worst_df_keys':worst,'caps':caps,
      'configuration':{'seed_source':'frozen V1 scores only','seed_thresholds':[.61,.80,.95],
        'neighbor_keys':'normalized name character 4-grams','rare_keys_per_seed_target':4,
        'country_partitioned':True,'per_target_K':{'S2':20,'S3':20},
        'per_seed_quotas':[5,10,20], 'prohibit_self':True,
        'tie_break':'shared grams DESC, rarity evidence DESC, target ID ASC',
        'directions':['S2→S3','S3→S2','S2→S2','S3→S3'],
        'max_temp_disk_bytes':MAX_TEMP_BYTES},
      'gt_used':False,'sealed_populations_accessed':[],'test_data_accessed':False}
    write_json(OUT/'neighbor_preflight.json',manifest)
    log('phase2_neighbor_preflight',population='v2_candidate_research',
        v2_research_label_read=False,manifest_sha256=sha(OUT/'neighbor_preflight.json'))
    print(json.dumps({k:v for k,v in manifest.items() if k not in ('df_distribution','worst_df_keys')},indent=2))
    c.close()


if __name__=='__main__':
    with Monitor(OUT/'tmp/phase2_neighbor_preflight') as monitor:
        run()
    write_json(OUT/'neighbor_preflight_resources.json',monitor.result())
