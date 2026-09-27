"""Research-only end-to-end evaluation of audited secondary cap contrasts."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.runtime import R,connect,log,sha,write_json
from v2_phase2_access import research_only
from v2_phase2_expand_evaluate import metrics_for_threshold,weights
import numpy as np
import pyarrow.parquet as pq

OUT=ROOT/'work/v2_phase2_r1'
CAP=OUT/'cap_sweep'
GT=R/'research_gt.parquet'
DIAG=R/'v1_link_diagnostics.parquet'
PLUS=R/'v1_plus_all.parquet'
BASE=OUT/'step0_plus_all_s1.parquet'
CAP_SCORE=OUT/'cap_new_scores/*.parquet'


def guard() -> tuple[dict,list[dict]]:
    if len(research_only())!=100000:raise PermissionError('Research allocation changed')
    if not (OUT/'expansion_evaluation.json').exists():
        raise RuntimeError('Matcher-seeded Step 1 must finish before cap evaluation')
    step0=json.loads((OUT/'step0_evaluation.json').read_text())
    manifest=json.loads((CAP/'manifest.json').read_text())
    score=json.loads((OUT/'cap_new_scores/manifest.json').read_text())
    if manifest['status']!='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED' or score['status']!='COMPLETE':
        raise RuntimeError('Cap candidate/score artifacts not complete')
    for item in manifest['parts']:
        if item['status']!='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED' or sha(ROOT/item['path'])!=item['sha256']:
            raise RuntimeError('Cap candidate checksum/audit failed before GT access')
    for item in score['parts']:
        if sha(ROOT/item['path'])!=item['sha256']:
            raise RuntimeError('Cap score checksum failed')
    if sha(BASE)!=step0['v1_plus_all']['per_s1_artifact']['sha256']:
        raise RuntimeError('Step 0 paired baseline changed')
    return step0,manifest['parts']


def run() -> None:
    step0,configs=guard()
    c=connect('phase2_caps_evaluation')
    c.execute("SET memory_limit='1500MB'")
    base_f05=np.asarray(pq.read_table(BASE,columns=['f05'])['f05'])
    w=weights(len(base_f05))
    c.execute(f"""CREATE TEMP TABLE base_support AS WITH hits AS (
      SELECT g.s1,count(*) recovered FROM read_parquet('{GT.as_posix()}') g
      JOIN read_parquet('{PLUS.as_posix()}') p
        ON g.s1=p.source1_entity_id AND g.mid=p.target_entity_id GROUP BY 1),
      counts AS (SELECT source1_entity_id s1,count(*) n
        FROM read_parquet('{PLUS.as_posix()}') GROUP BY 1)
      SELECT b.*,coalesce(h.recovered,0) recovered,coalesce(cc.n,0) baseline_candidates
      FROM read_parquet('{BASE.as_posix()}') b
      LEFT JOIN hits h USING(s1) LEFT JOIN counts cc USING(s1)""")
    remaining=c.sql(f"""SELECT count(*) FROM read_parquet('{GT.as_posix()}') g
      ANTI JOIN read_parquet('{PLUS.as_posix()}') b
        ON g.s1=b.source1_entity_id AND g.mid=b.target_entity_id""").fetchone()[0]
    if remaining!=34234:raise RuntimeError('Deduplicated remaining GT miss count differs')
    rank_remaining=c.sql(f"""SELECT count(*) FROM read_parquet('{DIAG.as_posix()}') d
      ANTI JOIN read_parquet('{PLUS.as_posix()}') b
        ON d.s1=b.source1_entity_id AND d.mid=b.target_entity_id
      WHERE d.miss_category='eligible_but_lost_through_ranking_cap'""").fetchone()[0]
    results={}
    for item in configs:
        name=item['configuration']['name']
        path=ROOT/item['path']
        c.execute(f"CREATE OR REPLACE TEMP VIEW current_new AS SELECT * FROM read_parquet('{path.as_posix()}')")
        new_count=item['rows']
        new_gt=c.sql(f"""SELECT count(*) FROM current_new x
          JOIN read_parquet('{GT.as_posix()}') g
            ON x.source1_entity_id=g.s1 AND x.target_entity_id=g.mid""").fetchone()[0]
        row=c.sql("""WITH n AS (SELECT source1_entity_id s1,count(*) n FROM current_new GROUP BY 1)
          SELECT avg(b.baseline_candidates+coalesce(n.n,0)),
            quantile_cont(b.baseline_candidates+coalesce(n.n,0),.95),
            quantile_cont(b.baseline_candidates+coalesce(n.n,0),.99),
            max(b.baseline_candidates+coalesce(n.n,0))
          FROM base_support b LEFT JOIN n USING(s1)""").fetchone()
        oracle=c.sql(f"""WITH h AS (SELECT x.source1_entity_id s1,count(*) n
          FROM current_new x JOIN read_parquet('{GT.as_posix()}') g
            ON x.source1_entity_id=g.s1 AND x.target_entity_id=g.mid GROUP BY 1)
          SELECT avg(CASE WHEN b.truth_n=0 THEN 1.0
            ELSE 1.25*(b.recovered+coalesce(h.n,0)) /
              (.25*b.truth_n+b.recovered+coalesce(h.n,0)) END)
          FROM base_support b LEFT JOIN h USING(s1)""").fetchone()[0]
        result={'configuration':item['configuration'],'population':'v2_candidate_research',
          'added_candidates':new_count,'union_candidate_count':18895613+new_count,
          'new_gt_beyond_plus_all':new_gt,
          'new_gt_per_1000_added':1000*new_gt/new_count if new_count else None,
          'candidate_pair_recall':(310916+new_gt)/345150,
          'candidate_oracle_macro_f05':oracle,
          'mean_union_candidates_per_s1':row[0],
          'p95_union_candidates_per_s1':row[1],
          'p99_union_candidates_per_s1':row[2],
          'max_union_candidates_per_s1':row[3],
          'frozen_matcher':metrics_for_threshold(c,name,.61,base_f05,w,step0,
            score_glob=CAP_SCORE,output_subdir='cap_metrics'),
          'candidate_artifact':{'path':item['path'],'sha256':item['sha256']}}
        results[name]=result
        print(name,'new',new_count,'GT',new_gt,'F05',result['frozen_matcher']['macro_f05'],flush=True)
    output={'status':'COMPLETE','population':'v2_candidate_research',
      'remaining_gt_misses_after_plus_all':remaining,
      'v1_rank_cap_category_remaining_after_plus_all':rank_remaining,
      'configs':'individual contrasts and two combined extremes; no unnecessary full Cartesian product',
      'configurations':results,
      'inputs':{'step0_sha256':sha(OUT/'step0_evaluation.json'),
        'cap_candidate_manifest_sha256':sha(CAP/'manifest.json'),
        'cap_score_manifest_sha256':sha(OUT/'cap_new_scores/manifest.json')},
      'labels_accessed':['v2_candidate_research'],
      'sealed_populations_accessed':[],'test_data_accessed':False}
    write_json(OUT/'cap_evaluation.json',output)
    log('phase2_cap_sweep_evaluated',population='v2_candidate_research',
      v2_research_label_read=True,metrics_sha256=sha(OUT/'cap_evaluation.json'))
    c.close()


if __name__=='__main__':run()
