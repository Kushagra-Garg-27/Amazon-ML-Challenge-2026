"""Bounded generation of frozen-policy-v1 candidates for matcher pilots.

This rebuilds the fixed inference ranking for explicitly selected S1 rows. It does
not read GT. The selected policy JSON is validated but never modified.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import duckdb

from .policies import load_policy
from .ranking import EVIDENCE_ORDER_SQL, HEAVY_ORDER_SQL

PILOT_SEED="feature_pilot_v1_20260926"

def connect(temp: Path):
    temp.mkdir(parents=True,exist_ok=True); c=duckdb.connect()
    memory_mb=int(os.environ.get("ER_DUCKDB_MEMORY_MB","700"))
    c.execute(f"SET memory_limit='{memory_mb}MB'; SET threads=1; SET preserve_insertion_order=false")
    c.execute(f"SET temp_directory='{temp.as_posix()}'"); return c

def select_s1(output: Path, train_n=5000, calibration_n=2000):
    c=connect(Path('work/feature_pilot_tmp'))
    sql=f"""WITH eligible AS (
      SELECT m.entity_id,m.split,k.country_norm
      FROM read_parquet('work/matcher_split_manifest.parquet') m
      JOIN read_parquet('work/keys/train_s1.parquet') k USING(entity_id)
      WHERE m.split IN ('model_train','model_calibration')
    ), ranked AS (
      SELECT *,row_number() OVER(PARTITION BY split ORDER BY md5(entity_id||':{PILOT_SEED}'),entity_id) rn
      FROM eligible)
    SELECT entity_id,split,country_norm FROM ranked
    WHERE (split='model_train' AND rn<={train_n}) OR
          (split='model_calibration' AND rn<={calibration_n}) ORDER BY split,entity_id"""
    partial=output.with_suffix('.partial.parquet'); partial.unlink(missing_ok=True)
    c.execute(f"COPY ({sql}) TO '{partial.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)"); os.replace(partial,output)
    out=dict(c.sql(f"SELECT split,count(*) FROM read_parquet('{output.as_posix()}') GROUP BY 1").fetchall()); c.close(); return out

def _target_sql(country: str) -> str:
    return " UNION ALL ".join(
      f"SELECT entity_id,country_norm,name_norm,name_nosuffix,name_sorted,addr_norm,num_tokens FROM read_parquet('work/keys/train_{s}.parquet') WHERE country_norm='{country}'"
      for s in ('s2','s3'))

def materialize_group(selection: Path, split: str, country: str, output: Path) -> dict:
    c=connect(output.parent/'tmp'); t0=time.time(); target=_target_sql(country)
    c.execute(f"""CREATE TEMP TABLE s1 AS SELECT k.entity_id s1,k.country_norm cc,k.name_norm,
      k.name_nosuffix,k.name_sorted,k.addr_norm,k.num_tokens
      FROM read_parquet('work/keys/train_s1.parquet') k JOIN read_parquet('{selection.as_posix()}') p
      ON k.entity_id=p.entity_id WHERE p.split=? AND p.country_norm=?""",[split,country])
    n_s1=c.sql("SELECT count(*) FROM s1").fetchone()[0]
    if not n_s1: raise RuntimeError(f"Empty pilot group {split}/{country}")
    c.execute(f"""CREATE TEMP TABLE sorted AS SELECT s.s1,t.entity_id mid
      FROM s1 s JOIN ({target}) t ON s.cc=t.country_norm AND length(s.name_sorted)>0 AND s.name_sorted=t.name_sorted""")
    c.execute(f"""CREATE TEMP TABLE exact_addr AS SELECT s.s1,t.entity_id mid
      FROM s1 s JOIN ({target}) t ON s.cc=t.country_norm AND length(s.addr_norm)>0 AND s.addr_norm=t.addr_norm""")
    for field,scol,tcol in [('name','name_nosuffix','name_nosuffix'),('addr','addr_norm','addr_norm')]:
        c.execute(f"""CREATE TEMP TABLE s1tok_{field} AS SELECT s1,cc,
          unnest(list_distinct(str_split({scol},' '))) tok FROM s1""")
        c.execute(f"""CREATE TEMP TABLE tgtok_{field} AS SELECT x.mid,x.cc,x.tok FROM (
          SELECT entity_id mid,country_norm cc,unnest(list_distinct(str_split({tcol},' '))) tok
          FROM ({target})) x
          JOIN read_parquet('work/freeze_gate/df_{field}.parquet') d
            ON x.cc=d.cc AND x.tok=d.tok AND d.df<=2000
          JOIN (SELECT DISTINCT tok FROM s1tok_{field} WHERE length(tok)>0) st ON x.tok=st.tok""")
        c.execute(f"""CREATE TEMP TABLE score_{field} AS SELECT s.s1,t.mid,count(*)::INT sh,
          sum(1.0/d.df) score FROM s1tok_{field} s JOIN tgtok_{field} t ON s.cc=t.cc AND s.tok=t.tok
          JOIN read_parquet('work/freeze_gate/df_{field}.parquet') d ON s.cc=d.cc AND s.tok=d.tok
          WHERE length(s.tok)>0 GROUP BY 1,2""")
        c.execute(f"""CREATE TEMP TABLE short_{field} AS SELECT s1,mid,sh,score FROM (
          SELECT *,row_number() OVER(PARTITION BY s1,substr(mid,1,2)
            ORDER BY score DESC,sh DESC,hash(mid),mid)::INT old_rsource FROM score_{field})
          WHERE old_rsource<=200""")
    for field,other,scol in [('name','addr','name_nosuffix'),('addr','name','addr_norm')]:
        count_col='name_nosuffix' if field=='name' else 'addr_norm'
        c.execute(f"""CREATE TEMP TABLE rank_{field} AS SELECT q.s1,q.mid,q.score,q.sh,
          row_number() OVER(PARTITION BY q.s1,substr(q.mid,1,2) ORDER BY
            q.both_pass DESC,q.postal_shared DESC,q.numeric_shared DESC,
            q.exact_name DESC,q.exact_addr DESC,round(q.score,12) DESC,q.sh DESC,
            q.coverage_s1 DESC,q.coverage_target DESC,q.jaccard DESC,q.mid ASC)::INT rsource
          FROM (SELECT r.s1 AS s1,r.mid AS mid,r.score AS score,r.sh AS sh,
            (o.mid IS NOT NULL) both_pass,
            (length(regexp_extract(s.addr_norm,'[0-9]{{5,6}}'))>0 AND regexp_extract(s.addr_norm,'[0-9]{{5,6}}')=regexp_extract(t.addr_norm,'[0-9]{{5,6}}')) postal_shared,
            (length(regexp_extract(s.addr_norm,'[0-9]+'))>0 AND regexp_extract(s.addr_norm,'[0-9]+')=regexp_extract(t.addr_norm,'[0-9]+')) numeric_shared,
            (length(s.name_nosuffix)>0 AND s.name_nosuffix=t.name_nosuffix) exact_name,
            (length(s.addr_norm)>0 AND s.addr_norm=t.addr_norm) exact_addr,
            r.sh::DOUBLE/nullif(len(list_distinct(str_split(s.{count_col},' '))),0) coverage_s1,
            r.sh::DOUBLE/nullif(len(list_distinct(str_split(t.{count_col},' '))),0) coverage_target,
            r.sh::DOUBLE/nullif(len(list_distinct(str_split(s.{count_col},' ')))+len(list_distinct(str_split(t.{count_col},' ')))-r.sh,0) jaccard
          FROM short_{field} r JOIN s1 s ON r.s1=s.s1 JOIN ({target}) t ON r.mid=t.entity_id
          LEFT JOIN short_{other} o ON r.s1=o.s1 AND r.mid=o.mid) q""")
    c.execute("""CREATE TEMP TABLE heavy_s1 AS SELECT s1,count(*) n FROM sorted GROUP BY 1 HAVING n>=120""")
    c.execute(f"""CREATE TEMP TABLE heavy_rank AS SELECT s1,mid,row_number() OVER(PARTITION BY s1 ORDER BY {HEAVY_ORDER_SQL})::INT rk
      FROM (SELECT z.s1,z.mid,
       (length(s.addr_norm)>0 AND s.addr_norm=t.addr_norm) exact_addr,
       (length(regexp_extract(s.addr_norm,'[0-9]{{5,6}}'))>0 AND regexp_extract(s.addr_norm,'[0-9]{{5,6}}')=regexp_extract(t.addr_norm,'[0-9]{{5,6}}')) postal_shared,
       (length(regexp_extract(s.addr_norm,'[0-9]+'))>0 AND regexp_extract(s.addr_norm,'[0-9]+')=regexp_extract(t.addr_norm,'[0-9]+')) numeric_shared,
       (length(s.name_nosuffix)>0 AND s.name_nosuffix=t.name_nosuffix) exact_name,
       len(list_intersect(list_distinct(str_split(s.addr_norm,' ')),list_distinct(str_split(t.addr_norm,' '))))::DOUBLE /
         nullif(len(list_distinct(list_concat(str_split(s.addr_norm,' '),str_split(t.addr_norm,' ')))),0) addr_jaccard
       FROM sorted z JOIN heavy_s1 h USING(s1) JOIN s1 s USING(s1) JOIN ({target}) t ON z.mid=t.entity_id)""")
    union=" UNION ALL ".join([
      "SELECT s1,mid,1 b,0::INT nr,0::INT ar,0.0::DOUBLE ns,0.0::DOUBLE ads FROM sorted",
      "SELECT s1,mid,2 b,0,0,0.0,0.0 FROM exact_addr",
      "SELECT s1,mid,4 b,rsource,0,score,0.0 FROM rank_name WHERE rsource<=50",
      "SELECT s1,mid,8 b,0,rsource,0.0,score FROM rank_addr WHERE rsource<=50"])
    final=f"""WITH u AS (SELECT s1,mid,bit_or(b)::UTINYINT provenance,
      max(nr)::USMALLINT name_token_rank,max(ar)::USMALLINT address_token_rank,
      max(ns)::FLOAT name_shared_idf,max(ads)::FLOAT address_shared_idf
      FROM ({union}) GROUP BY 1,2)
      SELECT u.s1 source1_entity_id,u.mid target_entity_id,u.provenance,u.name_token_rank,u.address_token_rank,
       CASE WHEN name_token_rank=0 THEN address_token_rank WHEN address_token_rank=0 THEN name_token_rank ELSE least(name_token_rank,address_token_rank) END::USMALLINT source_balanced_rank,
       (hs.s1 IS NOT NULL) heavy_sorted_block,u.name_shared_idf,u.address_shared_idf
      FROM u LEFT JOIN heavy_rank h USING(s1,mid) LEFT JOIN heavy_s1 hs USING(s1)
      WHERE h.rk IS NULL OR NOT (u.provenance=1 AND h.rk>100) ORDER BY 1,2"""
    partial=output.with_suffix('.partial.parquet'); partial.unlink(missing_ok=True)
    c.execute(f"COPY ({final}) TO '{partial.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)"); os.replace(partial,output)
    rows=c.sql(f"SELECT count(*) FROM read_parquet('{output.as_posix()}')").fetchone()[0]
    c.close(); return {"split":split,"country":country,"s1":n_s1,"candidates":rows,"bytes":output.stat().st_size,"wall_seconds":time.time()-t0}

def run(selection: Path, output_dir: Path):
    policy=load_policy('work/final_candidate_policy.json')
    if (policy.s2_quota,policy.s3_quota,policy.heavy_sorted_cap)!=(50,50,100): raise RuntimeError("Frozen policy mismatch")
    output_dir.mkdir(parents=True,exist_ok=True); c=duckdb.connect()
    groups=c.sql(f"SELECT DISTINCT split,country_norm FROM read_parquet('{selection.as_posix()}') ORDER BY 1,2").fetchall(); c.close()
    out=[]
    for split,country in groups:
        path=output_dir/f"{split}_{country}.parquet"
        if path.exists():
            c=duckdb.connect(); n=c.sql(f"SELECT count(*) FROM read_parquet('{path.as_posix()}')").fetchone()[0]; c.close(); out.append({"split":split,"country":country,"candidates":n,"bytes":path.stat().st_size,"resumed":True}); continue
        print(f"materializing {split}/{country}",flush=True); out.append(materialize_group(selection,split,country,path))
    manifest={"policy_id":"evidence_source_50_50_heavy100_v1","pilot_seed":PILOT_SEED,"groups":out,"candidates":sum(x['candidates'] for x in out)}
    (output_dir/'candidate_manifest.json').write_text(json.dumps(manifest,indent=2)+"\n",encoding='utf-8'); return manifest

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--selection',type=Path,default=Path('work/feature_pilot_s1.parquet')); ap.add_argument('--output-dir',type=Path,default=Path('work/feature_pilot_candidates')); ap.add_argument('--train-s1',type=int,default=5000); ap.add_argument('--calibration-s1',type=int,default=2000); a=ap.parse_args()
    if not a.selection.exists(): print(select_s1(a.selection,a.train_s1,a.calibration_s1),flush=True)
    print(json.dumps(run(a.selection,a.output_dir),indent=2))

if __name__=='__main__': main()
