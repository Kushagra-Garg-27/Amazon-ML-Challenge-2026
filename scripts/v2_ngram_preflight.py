"""Full-target frequency and join-volume profiles for name 2/3/4-grams.

This runs after research-only miss diagnostics. It does not materialize pairs or
choose a pass. Every target S2/S3 record is considered under the explicit
name-length filter. All joins here are counts/DF, bounded by 1 thread/700MB.
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


def run():
    os.chdir(ROOT)
    research,sealed=populations()
    opportunity=json.loads((R/'v1_opportunity.json').read_text())
    gate=json.loads((R/'pass_opportunity_gate.json').read_text())
    baseline=json.loads((R/'v1_baseline_manifest.json').read_text())
    if baseline['status']!='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED':
        raise PermissionError('V1 baseline not audited')
    c=connect('ngram_preflight')
    c.execute("""CREATE TEMP VIEW targets AS SELECT entity_id mid,country_norm cc,name_norm nm FROM read_parquet('work/keys/train_s2.parquet')
      UNION ALL SELECT entity_id,country_norm,name_norm FROM read_parquet('work/keys/train_s3.parquet')""")
    c.execute("""CREATE TEMP VIEW sources AS SELECT k.entity_id s1,k.country_norm cc,k.name_norm nm
      FROM read_parquet('work/keys/train_s1.parquet') k JOIN read_parquet('work/v2_candidate_research.parquet') r USING(entity_id)""")
    target_rows=c.sql('SELECT count(*) FROM targets').fetchone()[0]
    if target_rows != sum(baseline['inputs'][name]['rows'] for name in ('train_s2','train_s3')):
        raise RuntimeError('Complete target corpus row count changed')
    results=[]
    for n in (2,3,4):
        # Frequency counts alone; no candidate or GT link is joined.
        df=R/f'ngram_df_name_{n}.parquet'
        expression=duckdb_grams_sql('nm',n)
        c.execute(f"""COPY (SELECT cc,gram,count(*)::INT df FROM
          (SELECT cc,mid,{expression} gram FROM targets WHERE length(nm) BETWEEN {n} AND 64)
          WHERE length(gram)={n} GROUP BY 1,2 ORDER BY 1,2)
          TO '{df.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        c.execute(f"""CREATE TEMP TABLE source_grams_{n} AS SELECT s1,cc,{expression} gram
          FROM sources WHERE length(nm) BETWEEN {n} AND 64""")
        count=c.sql(f"SELECT count(*) FROM source_grams_{n}").fetchone()[0]
        dfrows=c.sql(f"SELECT count(*) FROM read_parquet('{df.as_posix()}')").fetchone()[0]
        worst=[dict(zip(['country','gram','df'],row)) for row in c.sql(
            f"SELECT cc,gram,df FROM read_parquet('{df.as_posix()}') ORDER BY df DESC,cc,gram LIMIT 20").fetchall()]
        candidates=[]
        for cap in (50,100,200,500,1000):
            size=c.sql(f"""WITH eligible AS (SELECT s1,s.cc,s.gram,d.df,
              row_number() OVER(PARTITION BY s1 ORDER BY d.df,s.gram) rn
              FROM source_grams_{n} s JOIN read_parquet('{df.as_posix()}') d
              ON s.cc=d.cc AND s.gram=d.gram WHERE d.df<={cap}),
              limited AS (SELECT * FROM eligible WHERE rn<=4),
              keys AS (SELECT cc,gram,count(*) source_n,max(df) target_df FROM limited GROUP BY 1,2)
              SELECT coalesce(sum(source_n*target_df),0),coalesce(max(source_n*target_df),0),
                     coalesce(sum(source_n),0),count(*) FROM keys""").fetchone()
            candidates.append(dict(df_cap=cap,top_grams_per_s1=4,
                                   estimated_join_rows=size[0],worst_key_join_rows=size[1],
                                   source_gram_rows=size[2],eligible_keys=size[3]))
        results.append({'n':n,'df_path':df.relative_to(ROOT).as_posix(),'df_sha256':sha(df),
                        'source_gram_rows':count,'target_df_keys':dfrows,'worst_keys':worst,
                        'bounded_options':candidates,
                        'missed_link_signal':opportunity[f'name_{n}gram_overlap']})
        print('profiled name',n,'grams; target keys',dfrows,'bounded options',candidates,flush=True)
    result={'status':'FULL_TARGET_DF_PREFLIGHT','target_corpus':'complete train S2/S3',
            'target_rows':target_rows,'research_s1':len(research),
            'source_name_filter':'character length between n and 64; explicit resource bound, independent of GT',
            'country_partitions':True,'results':results,'label_used_for_retrieval':False,
            'diagnostic_signal_used_only_to_decide_whether_to_test_a_pass':True,
            'baseline_manifest_sha256':sha(R/'v1_baseline_manifest.json'),
            'opportunity_sha256':sha(R/'v1_opportunity.json')}
    write_json(R/'ngram_preflight.json',result)
    log('ngram_preflight_complete',command='.venv\\Scripts\\python.exe -B scripts\\v2_ngram_preflight.py',
        v2_research_label_read=False,manifest_sha256=sha(R/'ngram_preflight.json'))


if __name__=='__main__':
    with Monitor(R/'tmp/ngram_preflight') as monitor: run()
    write_json(R/'ngram_preflight_resources.json',monitor.result())
