"""Research-only upper bounds for inference-selected one-stage sister expansion."""
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
    if baseline['status']!='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED':
        raise PermissionError('Audited frozen baseline required')
    c=connect('sister_preflight')
    c.execute("""CREATE TEMP VIEW base AS SELECT source1_entity_id s1,target_entity_id mid,
      source_balanced_rank r,name_shared_idf,address_shared_idf
      FROM read_parquet('work/v2_research/v1_candidates/*.parquet')""")
    c.execute("""CREATE TEMP VIEW truth AS SELECT s1,mid FROM read_parquet('work/v2_research/research_gt.parquet')""")
    c.execute("""CREATE TEMP VIEW missed AS SELECT g.s1,g.mid FROM truth g
      ANTI JOIN base b USING(s1,mid)""")
    result={'status':'RESEARCH_ONLY_SISTER_PREFLIGHT','missed_links':c.sql('SELECT count(*) FROM missed').fetchone()[0],
      'seed_policy':'frozen V1 source_balanced_rank <= K; inference available, no labels used to select seeds',
      'target_source':'source prefix from training corpus identity only',
      'one_stage_only':True,'ground_truth_used_for_opportunity_diagnosis_only':True,
      'tiers':[],'strict_global_seed_quota_tiers':[]}
    for k in (1,3,5,10):
        row=c.sql(f"""WITH selected AS (SELECT s1,mid FROM base WHERE r<={k}),
          true_seeds AS (SELECT s.s1,s.mid FROM selected s JOIN truth t USING(s1,mid)),
          opportunity AS (SELECT m.s1,m.mid,
            count(*) FILTER(WHERE substr(t.mid,1,2)<>substr(m.mid,1,2)) opposite_true_seeds,
            count(*) same_or_any_true_seeds
            FROM missed m JOIN true_seeds t ON m.s1=t.s1 GROUP BY 1,2)
          SELECT (SELECT count(*) FROM selected),
            (SELECT count(*) FROM true_seeds),
            count(*),count(*) FILTER(WHERE opposite_true_seeds>0)
          FROM opportunity""").fetchone()
        result['tiers'].append(dict(rank_cap=k,selected_seed_candidates=row[0],true_seed_candidates=row[1],
          missed_links_with_any_true_seed=row[2],missed_links_with_opposite_source_true_seed=row[3]))
    # Expansion key pressure: each extra seed requires target-to-target indexing.
    # Scale from measured source four-gram join and actual seed cardinality;
    # clearly label this as a rough bound rather than materialized performance.
    pre=json.loads((R/'ngram_preflight.json').read_text())
    name4=next(x for x in pre['results'] if x['n']==4)
    parent=next(x for x in name4['bounded_options'] if x['df_cap']==1000)
    per_source=parent['estimated_join_rows']/len(research)
    for tier in result['tiers']:
        tier['rough_name4_join_rows_at_same_key_density']=round(tier['selected_seed_candidates']*per_source)
    c.execute("""CREATE TEMP VIEW global_ranked AS SELECT s1,mid,
      row_number() OVER(PARTITION BY s1 ORDER BY r NULLS LAST,
        name_shared_idf DESC NULLS LAST,address_shared_idf DESC NULLS LAST,mid) seed_rank
      FROM base""")
    for k in (1,2,3):
        row=c.sql(f"""WITH selected AS (SELECT s1,mid FROM global_ranked WHERE seed_rank<={k}),
          true_seeds AS (SELECT s.s1,s.mid FROM selected s JOIN truth t USING(s1,mid)),
          opportunity AS (SELECT m.s1,m.mid,
            count(*) FILTER(WHERE substr(t.mid,1,2)<>substr(m.mid,1,2)) opposite_true_seeds
            FROM missed m JOIN true_seeds t ON m.s1=t.s1 GROUP BY 1,2)
          SELECT (SELECT count(*) FROM selected),(SELECT count(*) FROM true_seeds),
            count(*),count(*) FILTER(WHERE opposite_true_seeds>0) FROM opportunity""").fetchone()
        result['strict_global_seed_quota_tiers'].append(dict(max_seeds_per_s1=k,
          selected_seed_candidates=row[0],true_seed_candidates=row[1],
          missed_links_with_any_true_seed=row[2],
          missed_links_with_opposite_source_true_seed=row[3],
          rough_name4_join_rows_at_same_key_density=round(row[0]*per_source)))
    result['reference_fourgram_join_per_research_s1']=per_source
    write_json(R/'sister_preflight.json',result)
    log('sister_preflight_complete',command='.venv\\Scripts\\python.exe -B scripts\\v2_sister_preflight.py',
      v2_research_label_read=True,scope='v2_candidate_research only',manifest_sha256=sha(R/'sister_preflight.json'))
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    with Monitor(R/'tmp/sister_preflight') as m:run()
    write_json(R/'sister_preflight_resources.json',m.result())
