"""Research-only exact key eligibility versus materialized pass quota loss."""
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
    c=connect('pass_cap_diagnostics')
    c.execute("CREATE TEMP VIEW truth AS SELECT s1,mid FROM read_parquet('work/v2_research/research_gt.parquet')")
    c.execute("""CREATE TEMP VIEW baseline AS SELECT source1_entity_id s1,target_entity_id mid
      FROM read_parquet('work/v2_research/v1_candidates/*.parquet')""")
    c.execute("""CREATE TEMP VIEW targets AS SELECT entity_id mid,'s2' src,country_norm cc,
      name_norm nm_name,addr_norm addr FROM read_parquet('work/keys/train_s2.parquet')
      UNION ALL SELECT entity_id,'s3',country_norm,name_norm,addr_norm
      FROM read_parquet('work/keys/train_s3.parquet')""")
    c.execute("""CREATE TEMP VIEW sources AS SELECT k.entity_id s1,k.country_norm cc,
      k.name_norm nm_name,k.addr_norm addr
      FROM read_parquet('work/keys/train_s1.parquet') k
      JOIN read_parquet('work/v2_candidate_research.parquet') r USING(entity_id)""")
    name_gram=duckdb_grams_sql('nm',4)
    outcomes={}
    for kind in ('name_char4','acronym','postal_like_numeric','address_char4','sister_expansion'):
        manifest=json.loads((R/f'{kind}_manifest.json').read_text())
        if manifest['status']!='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED':
            raise PermissionError(f'{kind} pass must be independently audited')
        c.execute('DROP VIEW IF EXISTS eligible')
        if kind in ('acronym','postal_like_numeric'):
            raw=(R/f'{kind}_raw_hits.parquet').as_posix()
            c.execute(f"CREATE TEMP VIEW eligible AS SELECT DISTINCT s1,mid FROM read_parquet('{raw}')")
        elif kind in ('name_char4','address_char4'):
            if kind=='name_char4':
                col='nm_name';maxlen=64;cap=1000;df='work/v2_research/ngram_df_name_4.parquet';dfcol='gram'
            else:
                col='addr';maxlen=96;cap=200;df='work/v2_research/address_char4_df.parquet';dfcol='ky'
            c.execute(f"""CREATE OR REPLACE TEMP TABLE selected AS WITH grams AS (
              SELECT s1,cc,{name_gram} gram FROM
              (SELECT s1,cc,{col} nm FROM sources WHERE length({col}) BETWEEN 4 AND {maxlen})),
              ranked AS (SELECT g.*,d.df,row_number() OVER(PARTITION BY s1 ORDER BY d.df,g.gram) rn
              FROM grams g JOIN read_parquet('{df}') d ON g.cc=d.cc AND g.gram=d.{dfcol}
              WHERE d.df<={cap}) SELECT s1,cc,gram FROM ranked WHERE rn<=4""")
            c.execute(f"""CREATE TEMP VIEW eligible AS WITH gt_grams AS (
              SELECT s1,mid,cc,{name_gram} gram FROM
              (SELECT g.s1,g.mid,t.cc,t.{col} nm FROM truth g JOIN targets t USING(mid)
               WHERE length(t.{col}) BETWEEN 4 AND {maxlen}))
              SELECT DISTINCT g.s1,g.mid FROM gt_grams g JOIN selected s
              ON g.s1=s.s1 AND g.cc=s.cc AND g.gram=s.gram""")
        else:
            df='work/v2_research/ngram_df_name_4.parquet'
            seed='work/v2_research/sister_one_seed.parquet'
            c.execute(f"""CREATE OR REPLACE TEMP TABLE selected AS WITH grams AS (
              SELECT s.s1,s.seed_mid,t.src seed_source,t.cc,{name_gram} gram
              FROM read_parquet('{seed}') s JOIN
              (SELECT mid,src,cc,nm_name nm FROM targets) t ON s.seed_mid=t.mid
              WHERE length(t.nm) BETWEEN 4 AND 64), ranked AS (
              SELECT g.*,d.df,row_number() OVER(PARTITION BY s1 ORDER BY d.df,g.gram) rn
              FROM grams g JOIN read_parquet('{df}') d ON g.cc=d.cc AND g.gram=d.gram
              WHERE d.df<=1000) SELECT s1,cc,gram,seed_source FROM ranked WHERE rn<=4""")
            c.execute(f"""CREATE TEMP VIEW eligible AS WITH gt_grams AS (
              SELECT s1,mid,src,cc,{name_gram} gram FROM
              (SELECT g.s1,g.mid,t.src,t.cc,t.nm_name nm FROM truth g JOIN targets t USING(mid)
               WHERE length(t.nm_name) BETWEEN 4 AND 64))
              SELECT DISTINCT g.s1,g.mid FROM gt_grams g JOIN selected s
              ON g.s1=s.s1 AND g.cc=s.cc AND g.gram=s.gram AND g.src<>s.seed_source""")
        final=f"read_parquet('work/v2_research/{kind}_candidates/*.parquet')"
        def count(sql):return c.sql(sql).fetchone()[0]
        eligible=count('SELECT count(*) FROM truth g JOIN eligible e USING(s1,mid)')
        recovered=count(f"""SELECT count(*) FROM truth g JOIN {final} p
          ON g.s1=p.source1_entity_id AND g.mid=p.target_entity_id""")
        eligible_missed_v1=count("""SELECT count(*) FROM truth g JOIN eligible e USING(s1,mid)
          ANTI JOIN baseline b ON g.s1=b.s1 AND g.mid=b.mid""")
        recovered_missed_v1=count(f"""SELECT count(*) FROM truth g JOIN {final} p
          ON g.s1=p.source1_entity_id AND g.mid=p.target_entity_id
          ANTI JOIN baseline b ON g.s1=b.s1 AND g.mid=b.mid""")
        final_without_key=count(f"""SELECT count(*) FROM truth g JOIN {final} p
          ON g.s1=p.source1_entity_id AND g.mid=p.target_entity_id
          ANTI JOIN eligible e ON g.s1=e.s1 AND g.mid=e.mid""")
        if final_without_key or recovered>eligible or recovered_missed_v1>eligible_missed_v1:
            raise RuntimeError(f'{kind}: materialized true link lacks eligible key')
        outcomes[kind]={'eligible_gt_links_before_quota':eligible,'recovered_gt_links_after_quota':recovered,
          'gt_links_lost_to_ranking_quota':eligible-recovered,
          'eligible_v1_missed_gt_links_before_quota':eligible_missed_v1,
          'new_gt_links_after_quota':recovered_missed_v1,
          'incremental_gt_lost_to_ranking_quota':eligible_missed_v1-recovered_missed_v1,
          'materialized_true_links_without_eligible_key':final_without_key,
          'final_candidate_rows':manifest['rows']}
        print(kind,outcomes[kind],flush=True)
    write_json(R/'pass_cap_diagnostics.json',{'status':'RESEARCH_ONLY_EXACT_ELIGIBILITY_AND_CAP_LOSS',
      'research_s1':len(research),'complete_target_corpus_used_for_key_DF':True,
      'passes':outcomes})
    log('pass_cap_diagnostics_complete',command='.venv\\Scripts\\python.exe -B scripts\\v2_pass_cap_diagnostics.py',
      v2_research_label_read=True,scope='v2_candidate_research only',manifest_sha256=sha(R/'pass_cap_diagnostics.json'))


if __name__=='__main__':
    with Monitor(R/'tmp/pass_cap_diagnostics') as m:run()
    write_json(R/'pass_cap_diagnostics_resources.json',m.result())
