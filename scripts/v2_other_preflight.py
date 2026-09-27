"""Label-free full-target DF and join profiles for alternate retrieval keys."""
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
    baseline=json.loads((R/'v1_baseline_manifest.json').read_text())
    opportunity=json.loads((R/'v1_opportunity.json').read_text())
    if baseline['status']!='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED':
        raise PermissionError('Audited V1 baseline required')
    c=connect('other_preflight')
    c.execute("""CREATE TEMP VIEW targets AS SELECT entity_id mid,country_norm cc,
      name_acronym ac,addr_norm addr,num_tokens nums FROM read_parquet('work/keys/train_s2.parquet')
      UNION ALL SELECT entity_id,country_norm,name_acronym,addr_norm,num_tokens
      FROM read_parquet('work/keys/train_s3.parquet')""")
    c.execute("""CREATE TEMP VIEW sources AS SELECT k.entity_id s1,k.country_norm cc,
      k.name_acronym ac,k.addr_norm addr,k.num_tokens nums
      FROM read_parquet('work/keys/train_s1.parquet') k
      JOIN read_parquet('work/v2_candidate_research.parquet') r USING(entity_id)""")
    cases={
      'acronym':("SELECT cc,ac ky FROM targets WHERE length(ac) BETWEEN 3 AND 12",
                 "SELECT s1,cc,ac ky FROM sources WHERE length(ac) BETWEEN 3 AND 12",[20,50,100,200]),
      'postal_like_numeric':("SELECT cc,unnest(string_split(nums,',')) ky FROM targets WHERE nums<>''",
                 "SELECT s1,cc,unnest(string_split(nums,',')) ky FROM sources WHERE nums<>''",[20,50,100,200]),
      'address_char4':(f"SELECT cc,{duckdb_grams_sql('nm',4)} ky FROM (SELECT cc,addr nm FROM targets WHERE length(addr) BETWEEN 4 AND 96)",
                 f"SELECT s1,cc,{duckdb_grams_sql('nm',4)} ky FROM (SELECT s1,cc,addr nm FROM sources WHERE length(addr) BETWEEN 4 AND 96)",[20,50,100,200]),
    }
    outputs={}
    for case,(target_sql,source_sql,caps) in cases.items():
        condition="length(ky) BETWEEN 4 AND 6" if case=='postal_like_numeric' else 'true'
        df=R/f'{case}_df.parquet';pending=df.with_suffix('.pending.parquet')
        if pending.exists():pending.unlink()
        c.execute(f"""COPY (SELECT cc,ky,count(*)::INT df FROM ({target_sql})
          WHERE {condition} GROUP BY 1,2 ORDER BY 1,2)
          TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        os.replace(pending,df)
        c.execute(f"CREATE OR REPLACE TEMP TABLE src_keys AS SELECT DISTINCT s1,cc,ky FROM ({source_sql}) WHERE {condition}")
        source_count=c.sql('SELECT count(*) FROM src_keys').fetchone()[0]
        worst=[dict(zip(['country','key','df'],r)) for r in c.sql(f"SELECT cc,ky,df FROM read_parquet('{df.as_posix()}') ORDER BY df DESC,cc,ky LIMIT 12").fetchall()]
        options=[]
        for cap in caps:
            # One key/source for explicit bounded estimates. Address grams use
            # four rare keys, matching the name-gram preflight.
            max_keys=4 if case=='address_char4' else 1
            q=f"""WITH eligible AS (SELECT s.s1,s.cc,s.ky,d.df,
              row_number() OVER(PARTITION BY s.s1 ORDER BY d.df,s.ky) rn
              FROM src_keys s JOIN read_parquet('{df.as_posix()}') d USING(cc,ky)
              WHERE d.df<={cap}), limited AS (SELECT * FROM eligible WHERE rn<={max_keys}),
              k AS (SELECT cc,ky,count(*) sn,max(df) dn FROM limited GROUP BY 1,2)
              SELECT coalesce(sum(sn*dn),0),coalesce(max(sn*dn),0),coalesce(sum(sn),0),count(*) FROM k"""
            est=c.sql(q).fetchone()
            options.append(dict(df_cap=cap,max_keys_per_s1=max_keys,
                estimated_join_rows=est[0],worst_key_join_rows=est[1],
                selected_source_key_rows=est[2],eligible_keys=est[3]))
        outputs[case]={'target_df_path':df.relative_to(ROOT).as_posix(),'target_df_sha256':sha(df),
          'target_df_keys':c.sql(f"SELECT count(*) FROM read_parquet('{df.as_posix()}')").fetchone()[0],
          'source_key_rows':source_count,'worst_keys':worst,'options':options,
          'filters':('numeric token length 4..6' if case=='postal_like_numeric' else
                     'acronym length 3..12' if case=='acronym' else 'address length 4..96'),
          'complete_target_corpus_rows':sum(baseline['inputs'][n]['rows'] for n in ('train_s2','train_s3'))}
        print('preflight',case,'source keys',source_count,'options',options,flush=True)
    result={'status':'FULL_TARGET_ALTERNATE_KEY_PREFLIGHT','research_s1':len(research),
      'label_used_for_retrieval':False,'country_partitions':True,'cases':outputs,
      'missed_link_signals':{k:opportunity.get(k) for k in ('address_4gram_overlap','acronym_equal','initials_equal','numeric_overlap','postal_equal')},
      'baseline_manifest_sha256':sha(R/'v1_baseline_manifest.json')}
    write_json(R/'other_preflight.json',result)
    log('other_pass_preflight_complete',command='.venv\\Scripts\\python.exe -B scripts\\v2_other_preflight.py',
        v2_research_label_read=False,manifest_sha256=sha(R/'other_preflight.json'))


if __name__=='__main__':
    with Monitor(R/'tmp/other_preflight') as m:run()
    write_json(R/'other_preflight_resources.json',m.result())
