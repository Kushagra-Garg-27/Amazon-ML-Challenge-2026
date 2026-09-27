"""Research-label evaluation after every matcher-seeded candidate artifact is audited."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.runtime import R,connect,log,sha,write_json
from v2_phase2_access import research_only
import numpy as np
import pyarrow.parquet as pq

OUT=ROOT/'work/v2_phase2_r1'
GT=R/'research_gt.parquet'
TRUTH=R/'research_truth_counts.parquet'
PLUS=R/'v1_plus_all.parquet'
DIAG=R/'v1_link_diagnostics.parquet'
BASE=OUT/'step0_plus_all_s1.parquet'
EXP_SCORE=OUT/'expansion_new_scores/*.parquet'
SEED=20260927


def guard() -> tuple[dict,list[dict]]:
    research=research_only()
    if len(research)!=100000:raise PermissionError('Research seal changed')
    step0=json.loads((OUT/'step0_evaluation.json').read_text())
    if step0['status']!='COMPLETE' or sha(BASE)!=step0['v1_plus_all']['per_s1_artifact']['sha256']:
        raise RuntimeError('Step 0 baseline missing or changed')
    scores=json.loads((OUT/'expansion_new_scores/manifest.json').read_text())
    if scores['status']!='COMPLETE':raise RuntimeError('Expansion score artifact incomplete')
    for part in scores['parts']:
        if sha(ROOT/part['path'])!=part['sha256']:
            raise RuntimeError('Expansion score changed')
    configs=[]
    pre=json.loads((OUT/'neighbor_preflight.json').read_text())
    for cap in (1000,5000):
        option=pre['caps'][str(cap)]
        if not (option['under_hard_join_cap'] and option['under_temp_disk_cap']):continue
        grid=json.loads((OUT/'expansion_grid'/f'df{cap}_manifest.json').read_text())
        if grid['status']!='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED':
            raise RuntimeError('Grid not structurally audited before GT access')
        for x in grid['configurations']:
            if x['status']!='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED' or sha(ROOT/x['path'])!=x['sha256']:
                raise RuntimeError('Grid candidate changed before GT access')
            configs.append(x)
    if not configs:raise RuntimeError('No audited one-hop configurations')
    return step0,configs


def weights(n:int) -> np.ndarray:
    rng=np.random.default_rng(SEED)
    result=np.empty((1000,n),dtype=np.uint8)
    for i in range(1000):
        row=np.bincount(rng.integers(0,n,n),minlength=n)
        if row.max()>255:raise RuntimeError('Bootstrap weight overflow')
        result[i]=row
    return result


def bootstrap(delta:np.ndarray,w:np.ndarray) -> dict:
    sample=np.empty(1000,dtype=np.float64)
    for start in range(0,1000,50):
        sample[start:start+50]=w[start:start+50].astype(np.float32) @ delta.astype(np.float32) / len(delta)
    ci=np.quantile(sample,[.025,.975])
    return {'resamples':1000,'seed':SEED,'unit':'S1 entity',
      'point_delta':float(delta.mean()),'ci_95':[float(ci[0]),float(ci[1])],
      'ci_lower_bound':float(ci[0]),'ci_excludes_zero':bool(ci[0]>0 or ci[1]<0)}


def metrics_for_threshold(c,config_name:str,decision:float,base_f05:np.ndarray,w:np.ndarray,
                          step0:dict, score_glob:Path=EXP_SCORE,
                          output_subdir:str='expansion_metrics') -> dict:
    c.execute(f"""CREATE OR REPLACE TEMP VIEW accepted_new AS
      SELECT n.source1_entity_id s1,n.target_entity_id mid,s.score,s.target_is_s2,
        s.address_missing,s.script_conflict
      FROM current_new n JOIN read_parquet('{score_glob.as_posix()}') s
        ON n.source1_entity_id=s.source1_entity_id AND n.target_entity_id=s.target_entity_id
      WHERE s.score>={decision}""")
    outfile=OUT/output_subdir/f'{config_name}_decision_{int(decision*100):02d}_s1.parquet'
    outfile.parent.mkdir(exist_ok=True)
    pending=outfile.with_suffix('.pending.parquet')
    pending.unlink(missing_ok=True)
    c.execute(f"""COPY (WITH inc AS (
      SELECT a.s1,count(*) pred_add,count(*) FILTER(WHERE g.mid IS NOT NULL) tp_add
      FROM accepted_new a LEFT JOIN read_parquet('{GT.as_posix()}') g USING(s1,mid)
      GROUP BY 1), e AS (
      SELECT b.s1,b.truth_n,(b.pred_n+coalesce(i.pred_add,0)) pred_n,
        (b.tp+coalesce(i.tp_add,0)) tp,b.country,b.s1_address_missing
      FROM read_parquet('{BASE.as_posix()}') b LEFT JOIN inc i USING(s1))
      SELECT *,CASE WHEN truth_n=0 AND pred_n=0 THEN 1.0
        WHEN truth_n=0 THEN 0.0
        ELSE 1.25*tp/(0.25*truth_n+pred_n) END::DOUBLE f05
      FROM e ORDER BY s1)
      TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
    pending.replace(outfile)
    row=c.sql(f"""SELECT count(*),sum(truth_n),sum(pred_n),sum(tp),avg(f05),
      count(*) FILTER(WHERE truth_n=0 AND pred_n=0)::DOUBLE /
        nullif(count(*) FILTER(WHERE truth_n=0),0),
      avg((pred_n=0)::INT),avg(pred_n),
      count(*) FILTER(WHERE truth_n>1 AND pred_n<truth_n),
      count(*) FILTER(WHERE truth_n>1 AND pred_n>truth_n)
      FROM read_parquet('{outfile.as_posix()}')""").fetchone()
    names=('s1','gt_links','predicted','true_positive','macro_f05','singleton_accuracy',
      'empty_prediction_rate','mean_predicted_matches','multi_match_underprediction',
      'multi_match_overprediction')
    result=dict(zip(names,row))
    result['pair_precision']=result['true_positive']/result['predicted'] if result['predicted'] else None
    result['pair_recall']=result['true_positive']/result['gt_links']
    result['decision_threshold']=decision
    result['s1_slices']={}
    for field in ('country','s1_address_missing'):
        result['s1_slices'][field]=[dict(zip((field,'s1','macro_f05','singleton_accuracy'),r))
          for r in c.sql(f"""SELECT {field},count(*),avg(f05),
            count(*) FILTER(WHERE truth_n=0 AND pred_n=0)::DOUBLE /
              nullif(count(*) FILTER(WHERE truth_n=0),0)
            FROM read_parquet('{outfile.as_posix()}') GROUP BY 1 ORDER BY 1""").fetchall()]
    result['truth_link_slices']={}
    baseline=step0['v1_plus_all']['truth_link_slices']
    for field in ('target_source','country','both_address','script_relation'):
        add=dict(c.sql(f"""SELECT d.{field},count(*)
          FROM read_parquet('{DIAG.as_posix()}') d JOIN accepted_new a USING(s1,mid)
          GROUP BY 1""").fetchall())
        result['truth_link_slices'][field]=[{**x,'accepted':x['accepted']+add.get(x[field],0),
          'recall':(x['accepted']+add.get(x[field],0))/x['gt_links']}
          for x in baseline[field]]
    new_truth=c.sql(f"""SELECT count(*) FILTER(WHERE g.mid IS NOT NULL),
      count(*) FILTER(WHERE g.mid IS NULL)
      FROM accepted_new a LEFT JOIN read_parquet('{GT.as_posix()}') g USING(s1,mid)""").fetchone()
    result['final_true_positives_introduced']=new_truth[0]
    result['final_false_positives_introduced']=new_truth[1]
    table=pq.read_table(outfile,columns=['s1','f05'])
    if table['s1'].to_pylist()!=pq.read_table(BASE,columns=['s1'])['s1'].to_pylist():
        raise RuntimeError('Paired S1 ordering changed')
    delta=np.asarray(table['f05'])-base_f05
    result['paired_bootstrap_vs_plus_all']=bootstrap(delta,w)
    v1_artifact=ROOT/step0['v1']['per_s1_artifact']['path']
    v1_table=pq.read_table(v1_artifact,columns=['s1','f05'])
    if table['s1'].to_pylist()!=v1_table['s1'].to_pylist():
        raise RuntimeError('Paired V1 S1 ordering changed')
    result['paired_bootstrap_vs_v1']=bootstrap(np.asarray(table['f05'])-np.asarray(v1_table['f05']),w)
    result['per_s1_artifact']={'path':outfile.relative_to(ROOT).as_posix(),
      'sha256':sha(outfile),'rows':len(delta),'candidate_count':len(delta),
      'duplicate_count':0,'invalid_id_count':0,'population':'v2_candidate_research',
      'configuration':{'config':config_name,'decision_threshold':decision}}
    return result


def run() -> None:
    step0,configs=guard()
    c=connect('phase2_expansion_evaluation')
    c.execute("SET memory_limit='1500MB'")
    c.execute(f"""CREATE TEMP TABLE base_support AS WITH hits AS (
      SELECT g.s1,count(*) recovered FROM read_parquet('{GT.as_posix()}') g
      JOIN read_parquet('{PLUS.as_posix()}') p
        ON g.s1=p.source1_entity_id AND g.mid=p.target_entity_id GROUP BY 1),
      candidate_counts AS (SELECT source1_entity_id s1,count(*) n
        FROM read_parquet('{PLUS.as_posix()}') GROUP BY 1)
      SELECT b.*,coalesce(h.recovered,0) recovered,coalesce(cc.n,0) baseline_candidates
      FROM read_parquet('{BASE.as_posix()}') b
      LEFT JOIN hits h USING(s1) LEFT JOIN candidate_counts cc USING(s1)""")
    base_table=pq.read_table(BASE,columns=['f05'])
    base_f05=np.asarray(base_table['f05'])
    w=weights(len(base_f05))
    results={}
    for item in configs:
        cfg=item['configuration']
        name=Path(item['path']).stem
        c.execute(f"CREATE OR REPLACE TEMP VIEW expansion AS SELECT * FROM read_parquet('{(ROOT/item['path']).as_posix()}')")
        c.execute(f"""CREATE OR REPLACE TEMP TABLE current_new AS
          SELECT x.* FROM expansion x ANTI JOIN read_parquet('{PLUS.as_posix()}') b
          ON x.source1_entity_id=b.source1_entity_id AND x.target_entity_id=b.target_entity_id""")
        counts=c.sql("SELECT count(*) FROM expansion").fetchone()[0]
        new_count=c.sql('SELECT count(*) FROM current_new').fetchone()[0]
        recovered=c.sql(f"""SELECT count(*) FROM expansion x JOIN read_parquet('{GT.as_posix()}') g
          ON x.source1_entity_id=g.s1 AND x.target_entity_id=g.mid""").fetchone()[0]
        new_gt=c.sql(f"""SELECT count(*) FROM current_new x JOIN read_parquet('{GT.as_posix()}') g
          ON x.source1_entity_id=g.s1 AND x.target_entity_id=g.mid""").fetchone()[0]
        opportunity=c.sql(f"""SELECT count(*) FROM current_new x
          JOIN read_parquet('{DIAG.as_posix()}') d
            ON x.source1_entity_id=d.s1 AND x.target_entity_id=d.mid
          WHERE d.true_sister_retrieved AND NOT d.recovered""").fetchone()[0]
        directions=[dict(zip(('direction','expansion_pairs','new_pairs'),row)) for row in c.sql(f"""
          WITH all_dir AS (SELECT direction,count(*) n FROM expansion GROUP BY 1),
          new_dir AS (SELECT direction,count(*) n FROM current_new GROUP BY 1)
          SELECT a.direction,a.n,coalesce(n.n,0) FROM all_dir a LEFT JOIN new_dir n USING(direction)
          ORDER BY a.direction""").fetchall()]
        distribution=c.sql("""WITH n AS (SELECT source1_entity_id s1,count(*) n FROM current_new GROUP BY 1)
          SELECT avg(b.baseline_candidates+coalesce(n.n,0)),
            quantile_cont(b.baseline_candidates+coalesce(n.n,0),.95),
            quantile_cont(b.baseline_candidates+coalesce(n.n,0),.99),
            max(b.baseline_candidates+coalesce(n.n,0))
          FROM base_support b LEFT JOIN n USING(s1)""").fetchone()
        oracle=c.sql(f"""WITH hits AS (SELECT x.source1_entity_id s1,count(*) n
          FROM current_new x JOIN read_parquet('{GT.as_posix()}') g
            ON x.source1_entity_id=g.s1 AND x.target_entity_id=g.mid GROUP BY 1)
          SELECT avg(CASE WHEN b.truth_n=0 THEN 1.0
            ELSE 1.25*(b.recovered+coalesce(h.n,0)) /
              (.25*b.truth_n+b.recovered+coalesce(h.n,0)) END)
          FROM base_support b LEFT JOIN hits h USING(s1)""").fetchone()[0]
        cap=cfg['df_cap']
        edge=OUT/f'seed_neighbor_edges_df{cap}.parquet'
        false_seed=c.sql(f"""WITH e AS (SELECT e.s1,e.seed_mid,e.neighbor_mid
            FROM read_parquet('{edge.as_posix()}') e
            WHERE e.seed_score>={cfg['seed_threshold']}
              AND e.seed_quota_rank<={cfg['per_seed_quota']}),
          x AS (SELECT e.*,g.mid IS NOT NULL true_seed FROM e
            LEFT JOIN read_parquet('{GT.as_posix()}') g
              ON e.s1=g.s1 AND e.seed_mid=g.mid)
          SELECT count(*) FILTER(WHERE true_seed),
            count(*) FILTER(WHERE NOT true_seed),
            count(DISTINCT(s1,seed_mid)) FILTER(WHERE true_seed),
            count(DISTINCT(s1,seed_mid)) FILTER(WHERE NOT true_seed)
          FROM x""").fetchone()
        seed_population=c.sql(f"""SELECT count(*) FILTER(WHERE g.mid IS NOT NULL),
          count(*) FILTER(WHERE g.mid IS NULL)
          FROM read_parquet('{(OUT/'matcher_seeds_061.parquet').as_posix()}') s
          LEFT JOIN read_parquet('{GT.as_posix()}') g
            ON s.s1=g.s1 AND s.seed_mid=g.mid
          WHERE s.score>={cfg['seed_threshold']}""").fetchone()
        result={'configuration':cfg,'population':'v2_candidate_research',
          'expansion_candidates':counts,'already_in_plus_all':counts-new_count,
          'genuinely_new_candidates':new_count,'union_candidate_count':18895613+new_count,
          'mean_union_candidates_per_s1':distribution[0],
          'p95_union_candidates_per_s1':distribution[1],
          'p99_union_candidates_per_s1':distribution[2],
          'max_union_candidates_per_s1':distribution[3],
          'expansion_gt_recovered':recovered,'expansion_gt_already_in_plus_all':recovered-new_gt,
          'new_gt_beyond_plus_all':new_gt,
          'new_gt_per_1000_genuine_candidates':1000*new_gt/new_count if new_count else None,
          'candidate_pair_recall':(310916+new_gt)/345150,
          'candidate_oracle_macro_f05':oracle,
          'diagnostic_sister_opportunities_recovered':opportunity,
          'fraction_of_37866_opportunities':opportunity/37866,
          'candidate_precision_expansion_only':new_gt/new_count if new_count else None,
          'source_directions':directions,
          'false_seed_audit':{'true_seeds':seed_population[0],
            'false_seeds':seed_population[1],
            'true_seed_edges':false_seed[0],
            'false_seed_edges':false_seed[1],
            'true_seeds_with_edges':false_seed[2],
            'false_seeds_with_edges':false_seed[3],
            'candidate_edges_per_true_seed':false_seed[0]/seed_population[0] if seed_population[0] else None,
            'candidate_edges_per_false_seed':false_seed[1]/seed_population[1] if seed_population[1] else None,
            'false_seed_propagation_rate':false_seed[3]/seed_population[1] if seed_population[1] else None},
          'candidate_artifact':{'path':item['path'],'sha256':item['sha256']},
          'primary_frozen_v1':metrics_for_threshold(c,name,.61,base_f05,w,step0),
          'research_tuned_optimistic':{}}
        # Predeclared serious points: highest quota at each seed threshold.
        # These rules select no configuration using GT outcomes.
        if cfg['per_seed_quota']==20:
            for t2 in (.30,.45):
                result['research_tuned_optimistic'][str(t2)]=metrics_for_threshold(
                    c,name,t2,base_f05,w,step0)
            result['research_tuned_optimistic']['0.61']=result['primary_frozen_v1']
        results[name]=result
        print(name,'new',new_count,'GT',new_gt,'F05',result['primary_frozen_v1']['macro_f05'],flush=True)
    output={'status':'COMPLETE','population':'v2_candidate_research',
      'primary_decision_threshold':.61,'research_tuned_thresholds':[.30,.45,.61],
      'serious_config_rule':'predeclared per-seed quota 20 at each seed threshold and feasible DF cap',
      'configurations':results,
      'bootstrap':{'resamples':1000,'seed':SEED,'unit':'S1 entity'},
      'labels_accessed':['v2_candidate_research'],'sealed_populations_accessed':[],
      'test_data_accessed':False,
      'inputs':{'gt_sha256':sha(GT),'step0_sha256':sha(OUT/'step0_evaluation.json'),
        'expansion_score_manifest_sha256':sha(OUT/'expansion_new_scores/manifest.json')}}
    write_json(OUT/'expansion_evaluation.json',output)
    log('phase2_expansion_grid_evaluated',population='v2_candidate_research',
      v2_research_label_read=True,metrics_sha256=sha(OUT/'expansion_evaluation.json'))
    c.close()


if __name__=='__main__':
    run()
