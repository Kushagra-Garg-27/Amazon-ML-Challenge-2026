"""Label-free strict-one-seed sister expansion join preflight."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.ngram import duckdb_grams_sql
from er.candidates_v2.runtime import R,W,sha,write_json,log,connect,populations,Monitor


def run():
    os.chdir(ROOT)
    research,sealed=populations()
    name=json.loads((R/'ngram_preflight.json').read_text())
    df=next(x for x in name['results'] if x['n']==4)
    if sha(ROOT/df['df_path'])!=df['df_sha256']:
        raise RuntimeError('Full target name4 DF changed')
    c=connect('sister_seed_preflight')
    seed_file=R/'sister_one_seed.parquet';pending=seed_file.with_suffix('.pending.parquet')
    if pending.exists():pending.unlink()
    c.execute(f"""COPY (WITH ranked AS (
      SELECT source1_entity_id s1,target_entity_id seed_mid,
        row_number() OVER(PARTITION BY source1_entity_id ORDER BY
          source_balanced_rank NULLS LAST,name_shared_idf DESC NULLS LAST,
          address_shared_idf DESC NULLS LAST,target_entity_id) seed_rank
      FROM read_parquet('work/v2_research/v1_candidates/*.parquet'))
      SELECT s1,seed_mid FROM ranked WHERE seed_rank=1 ORDER BY s1)
      TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
    os.replace(pending,seed_file)
    c.execute("""CREATE TEMP VIEW targets AS SELECT entity_id mid,'s2' target_source,country_norm cc,name_norm nm
      FROM read_parquet('work/keys/train_s2.parquet') UNION ALL
      SELECT entity_id,'s3',country_norm,name_norm FROM read_parquet('work/keys/train_s3.parquet')""")
    expr=duckdb_grams_sql('nm',4)
    c.execute(f"""CREATE TEMP TABLE selected AS WITH source_grams AS (
      SELECT s.s1,s.seed_mid,t.target_source seed_source,t.cc,{expr} gram
      FROM read_parquet('{seed_file.as_posix()}') s JOIN targets t ON s.seed_mid=t.mid
      WHERE length(t.nm) BETWEEN 4 AND 64), eligible AS (
      SELECT g.*,d.df,row_number() OVER(PARTITION BY s1 ORDER BY d.df,g.gram) rn
      FROM source_grams g JOIN read_parquet('{df['df_path']}') d
      ON g.cc=d.cc AND g.gram=d.gram WHERE d.df<=1000)
      SELECT * FROM eligible WHERE rn<=4""")
    estimates=c.sql("""WITH x AS (SELECT cc,gram,count(*) sn,max(df) dn FROM selected GROUP BY 1,2)
      SELECT coalesce(sum(sn*dn),0),coalesce(max(sn*dn),0),count(*) FROM x""").fetchone()
    n_seeds=c.sql(f"SELECT count(*) FROM read_parquet('{seed_file.as_posix()}')").fetchone()[0]
    selected_s1=c.sql('SELECT count(DISTINCT s1) FROM selected').fetchone()[0]
    result={'status':'LABEL_FREE_STRICT_ONE_SEED_PREFLIGHT','seed_selection':'global top one by frozen V1 rank, name IDF, address IDF, target ID',
      'seed_file':seed_file.relative_to(ROOT).as_posix(),'seed_file_sha256':sha(seed_file),
      'seed_count':n_seeds,'selected_s1_with_eligible_keys':selected_s1,
      'fourgram_df_cap':1000,'top_rare_grams_per_seed':4,
      'estimated_same_country_all_source_join_rows_upper_bound':estimates[0],
      'worst_key_join_rows':estimates[1],'keys':estimates[2],
      'opposite_source_requirement_will_reduce_join':True,
      'full_target_rows':name['target_rows'],'labels_read':False,
      'name_df_sha256':df['df_sha256'],'baseline_candidates':'frozen V1'}
    write_json(R/'sister_seed_key_preflight.json',result)
    log('sister_seed_key_preflight_complete',command='.venv\\Scripts\\python.exe -B scripts\\v2_sister_seed_preflight.py',
      v2_research_label_read=False,manifest_sha256=sha(R/'sister_seed_key_preflight.json'))
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    with Monitor(R/'tmp/sister_seed_key_preflight') as m:run()
    write_json(R/'sister_seed_key_preflight_resources.json',m.result())
