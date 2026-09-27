"""Full-target short house-number key pressure, label free."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.runtime import R,W,sha,write_json,log,connect,populations,Monitor


def run():
    os.chdir(ROOT)
    research,sealed=populations()
    baseline=json.loads((R/'v1_baseline_manifest.json').read_text())
    c=connect('house_preflight')
    c.execute("""CREATE TEMP VIEW targets AS SELECT entity_id mid,country_norm cc,num_tokens nums
      FROM read_parquet('work/keys/train_s2.parquet') UNION ALL
      SELECT entity_id,country_norm,num_tokens FROM read_parquet('work/keys/train_s3.parquet')""")
    c.execute("""CREATE TEMP VIEW sources AS SELECT k.entity_id s1,k.country_norm cc,k.num_tokens nums
      FROM read_parquet('work/keys/train_s1.parquet') k
      JOIN read_parquet('work/v2_candidate_research.parquet') r USING(entity_id)""")
    df=R/'short_house_numeric_df.parquet';pending=df.with_suffix('.pending.parquet')
    if pending.exists():pending.unlink()
    c.execute(f"""COPY (SELECT cc,ky,count(*)::INT df FROM
      (SELECT cc,unnest(string_split(nums,',')) ky FROM targets WHERE nums<>'' )
      WHERE length(ky) BETWEEN 1 AND 3 GROUP BY 1,2 ORDER BY 1,2)
      TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
    os.replace(pending,df)
    c.execute("""CREATE TEMP TABLE source_keys AS SELECT DISTINCT s1,cc,ky FROM
      (SELECT s1,cc,unnest(string_split(nums,',')) ky FROM sources WHERE nums<>'')
      WHERE length(ky) BETWEEN 1 AND 3""")
    options=[]
    for cap in (20,50,100,200,500,1000):
        row=c.sql(f"""WITH eligible AS (SELECT s.*,d.df,
          row_number() OVER(PARTITION BY s1 ORDER BY d.df,ky) rn
          FROM source_keys s JOIN read_parquet('{df.as_posix()}') d USING(cc,ky)
          WHERE d.df<={cap}), selected AS (SELECT * FROM eligible WHERE rn=1),
          k AS (SELECT cc,ky,count(*) sn,max(df) dn FROM selected GROUP BY 1,2)
          SELECT (SELECT count(*) FROM selected),coalesce(sum(sn*dn),0),
            coalesce(max(sn*dn),0) FROM k""").fetchone()
        options.append(dict(df_cap=cap,source_s1_with_key=row[0],estimated_join_rows=row[1],
          worst_key_join_rows=row[2]))
    worst=[dict(zip(['country','short_number','target_df'],r)) for r in c.sql(f"""SELECT cc,ky,df
      FROM read_parquet('{df.as_posix()}') ORDER BY df DESC,cc,ky LIMIT 20""").fetchall()]
    result={'status':'FULL_TARGET_SHORT_HOUSE_NUMBER_PREFLIGHT','research_s1':len(research),
      'complete_target_rows':baseline['inputs']['train_s2']['rows']+baseline['inputs']['train_s3']['rows'],
      'df_path':df.relative_to(ROOT).as_posix(),'df_sha256':sha(df),
      'source_key_rows':c.sql('SELECT count(*) FROM source_keys').fetchone()[0],
      'worst_short_number_blocks':worst,'options':options,'labels_read':False,
      'scope':'one short numeric token per research S1; country partitioned; no target-pair materialization'}
    write_json(R/'short_house_preflight.json',result)
    log('short_house_preflight_complete',command='.venv\\Scripts\\python.exe -B scripts\\v2_house_preflight.py',
      v2_research_label_read=False,manifest_sha256=sha(R/'short_house_preflight.json'))
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    with Monitor(R/'tmp/short_house_preflight') as m:run()
    write_json(R/'short_house_preflight_resources.json',m.result())
