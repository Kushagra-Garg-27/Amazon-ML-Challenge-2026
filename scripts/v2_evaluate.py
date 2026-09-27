"""Exact research-only evaluation of materialized candidate operating points."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.runtime import R,W,sha,write_json,log,connect,populations,Monitor


def policy_sources():
    candidates=R/'v1_candidates/*.parquet'
    results={'v1_baseline':{'glob':candidates.as_posix(),'kind':'baseline'}}
    for name in ('name_char4','acronym','postal_like_numeric','address_char4','sister_expansion','multiseed_sister'):
        manifest=R/f'{name}_manifest.json'
        if not manifest.exists():continue
        proof=json.loads(manifest.read_text())
        if proof['status']!='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED':
            raise RuntimeError('V2 pass artifact not fully audited')
        for part in proof['parts']:
            if sha(ROOT/part['path'])!=part['sha256']:
                raise RuntimeError('V2 pass checksum differs')
        results[f'{name}_standalone']={'glob':(R/f'{name}_candidates/*.parquet').as_posix(),'kind':'pass','pass_name':name}
    return results


def materialize_union(c,baseline,passes,name):
    output=R/f'{name}.parquet'
    pending=output.with_suffix('.pending.parquet')
    if pending.exists():pending.unlink()
    expressions=[f"SELECT source1_entity_id s1,target_entity_id mid,1 pass_bit FROM read_parquet('{baseline}')"]
    for i,glob in enumerate(passes):
        expressions.append(f"SELECT source1_entity_id,target_entity_id,{1<<(i+1)} FROM read_parquet('{glob}')")
    union=' UNION ALL '.join(expressions)
    sql=f"""WITH p AS (
      {union}
    ) SELECT s1 source1_entity_id,mid target_entity_id,bit_or(pass_bit)::UTINYINT provenance_mask
      FROM p GROUP BY 1,2 ORDER BY 1,2"""
    start=time.monotonic()
    c.execute(f"COPY ({sql}) TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)")
    os.replace(pending,output)
    # A union is a new materialized operating point. Verify its own exact
    # artifact before any research-label metric is allowed to inspect it.
    rows,identities,outside,missing,country=c.sql(f"""SELECT count(*),
      count(DISTINCT(p.source1_entity_id,p.target_entity_id)),
      count(*) FILTER(WHERE r.entity_id IS NULL),
      count(*) FILTER(WHERE t.mid IS NULL),
      count(*) FILTER(WHERE s.country_norm<>t.cc)
      FROM read_parquet('{output.as_posix()}') p
      LEFT JOIN read_parquet('work/v2_candidate_research.parquet') r
      ON p.source1_entity_id=r.entity_id
      LEFT JOIN read_parquet('work/keys/train_s1.parquet') s
      ON p.source1_entity_id=s.entity_id
      LEFT JOIN (SELECT entity_id mid,country_norm cc FROM read_parquet('work/keys/train_s2.parquet')
                 UNION ALL SELECT entity_id,country_norm FROM read_parquet('work/keys/train_s3.parquet')) t
      ON p.target_entity_id=t.mid""").fetchone()
    audit={'status':'PASS' if rows==identities and not (outside or missing or country) else 'FAIL',
      'path':output.relative_to(ROOT).as_posix(),'sha256':sha(output),'rows':rows,
      'duplicate_pairs':rows-identities,'outside_research':outside,
      'absent_target':missing,'country_mismatch':country,'labels_read':False}
    write_json(R/f'{name}_structural_audit.json',audit)
    if audit['status']!='PASS':raise RuntimeError(f'Combination structural audit failed: {name}')
    return output,time.monotonic()-start


def eval_one(c,name,glob,baseline):
    relation=f"read_parquet('{glob}')"
    dup,nonresearch=c.sql(f"""SELECT count(*)-count(DISTINCT(source1_entity_id,target_entity_id)),
      count(*) FILTER(WHERE r.entity_id IS NULL) FROM {relation} x
      LEFT JOIN read_parquet('work/v2_candidate_research.parquet') r ON x.source1_entity_id=r.entity_id""").fetchone()
    if dup or nonresearch: raise RuntimeError(f'{name}: duplicate or nonresearch candidate')
    c.execute(f"CREATE OR REPLACE TEMP VIEW active AS SELECT source1_entity_id s1,target_entity_id mid FROM {relation}")
    entity=c.sql("""WITH n AS (SELECT s1,count(*) c FROM active GROUP BY 1),
      h AS (SELECT g.s1,count(*) hit FROM read_parquet('work/v2_research/research_gt.parquet') g JOIN active a USING(s1,mid) GROUP BY 1),
      e AS (SELECT t.s1,t.truth_n,coalesce(n.c,0) candidates,coalesce(h.hit,0) hits,
            CASE WHEN t.truth_n=0 THEN 1.0 ELSE 1.25*coalesce(h.hit,0)/(.25*t.truth_n+coalesce(h.hit,0)) END oracle_f05
            FROM read_parquet('work/v2_research/research_truth_counts.parquet') t
            LEFT JOIN n USING(s1) LEFT JOIN h USING(s1))
      SELECT count(*),sum(candidates),avg(candidates),median(candidates),
        quantile_cont(candidates,.90),quantile_cont(candidates,.95),quantile_cont(candidates,.99),max(candidates),
        sum(truth_n),sum(hits),avg(oracle_f05),
        count(*) FILTER(WHERE truth_n>0 AND hits=truth_n),
        count(*) FILTER(WHERE truth_n>0 AND hits>0 AND hits<truth_n),
        count(*) FILTER(WHERE truth_n>0 AND hits=0),count(*) FILTER(WHERE truth_n=0)
      FROM e""").fetchone()
    labels=['s1','candidate_count','mean_candidates_per_s1','median','p90','p95','p99','max',
            'gt_links','gt_recovered','oracle_macro_f05','all_recovered_s1','some_recovered_s1',
            'none_recovered_s1','singleton_s1']
    result=dict(zip(labels,entity))
    result['pair_candidate_recall']=result['gt_recovered']/result['gt_links']
    result['materialized_artifact']={'path':glob,'bytes':sum(p.stat().st_size for p in Path(glob).parent.glob(Path(glob).name))}
    if '*' not in glob: result['materialized_artifact']['sha256']=sha(glob)
    result['comparisons_vs_v1']={}
    for key,sql in {
        'candidate_added':f"SELECT count(*) FROM active a ANTI JOIN read_parquet('{baseline}') b ON a.s1=b.source1_entity_id AND a.mid=b.target_entity_id",
        'candidate_removed':f"SELECT count(*) FROM read_parquet('{baseline}') b ANTI JOIN active a ON a.s1=b.source1_entity_id AND a.mid=b.target_entity_id",
        'gt_added':f"""SELECT count(*) FROM read_parquet('work/v2_research/research_gt.parquet') g JOIN active a USING(s1,mid)
          ANTI JOIN read_parquet('{baseline}') b ON g.s1=b.source1_entity_id AND g.mid=b.target_entity_id""",
        'gt_lost':f"""SELECT count(*) FROM read_parquet('work/v2_research/research_gt.parquet') g
          JOIN read_parquet('{baseline}') b ON g.s1=b.source1_entity_id AND g.mid=b.target_entity_id
          ANTI JOIN active a USING(s1,mid)""",
    }.items(): result['comparisons_vs_v1'][key]=c.sql(sql).fetchone()[0]
    x=result['comparisons_vs_v1']
    x['net_gt_change']=x['gt_added']-x['gt_lost']
    x['gt_recovered_per_1000_added']=1000*x['gt_added']/x['candidate_added'] if x['candidate_added'] else None
    result['link_slices']={}
    for field in ('country','target_source','both_address','script_relation','truth_n'):
        result['link_slices'][field]=[dict(zip([field,'gt_links','recovered','recall'],r)) for r in c.sql(f"""SELECT d.{field},count(*),sum((a.s1 IS NOT NULL)::INT),avg((a.s1 IS NOT NULL)::INT)
          FROM read_parquet('work/v2_research/v1_link_diagnostics.parquet') d
          LEFT JOIN active a ON d.s1=a.s1 AND d.mid=a.mid GROUP BY 1 ORDER BY 1""").fetchall()]
    result['country_volume']=[dict(zip(['country','s1','mean','p95','p99','max'],r)) for r in c.sql("""WITH counts AS (
       SELECT r.entity_id s1,k.country_norm country,coalesce(n.n,0) c
       FROM read_parquet('work/v2_candidate_research.parquet') r JOIN read_parquet('work/keys/train_s1.parquet') k USING(entity_id)
       LEFT JOIN (SELECT s1,count(*) n FROM active GROUP BY 1) n ON r.entity_id=n.s1)
       SELECT country,count(*),avg(c),quantile_cont(c,.95),quantile_cont(c,.99),max(c) FROM counts GROUP BY 1 ORDER BY 1""").fetchall()]
    return result


def nondominated(results):
    keys=list(results)
    frontier=[]
    for name in keys:
        x=results[name]
        dominated=False
        for other in keys:
            if other==name: continue
            y=results[other]
            better_or_equal=(y['pair_candidate_recall']>=x['pair_candidate_recall'] and
                y['oracle_macro_f05']>=x['oracle_macro_f05'] and
                y['mean_candidates_per_s1']<=x['mean_candidates_per_s1'] and
                y['p99']<=x['p99'])
            strict=(y['pair_candidate_recall']>x['pair_candidate_recall'] or
                y['oracle_macro_f05']>x['oracle_macro_f05'] or
                y['mean_candidates_per_s1']<x['mean_candidates_per_s1'] or
                y['p99']<x['p99'])
            if better_or_equal and strict: dominated=True;break
        if not dominated: frontier.append(name)
    return frontier


def run():
    os.chdir(ROOT)
    research,sealed=populations()
    if not (R/'research_gt_receipt.json').exists() or not (R/'v1_link_diagnostics.parquet').exists():
        raise PermissionError('Research-only GT audit and diagnostics must finish')
    sources=policy_sources(); c=connect('policy_evaluate')
    baseline=sources['v1_baseline']['glob']
    standalones=[(name,info) for name,info in sources.items() if info['kind']=='pass']
    for name,info in standalones:
        union_name=f"v1_plus_{info['pass_name']}"
        joined,seconds=materialize_union(c,baseline,[info['glob']],union_name)
        sources[union_name]={'glob':joined.as_posix(),'kind':'union','union_wall_seconds':seconds}
    if len(standalones)>1:
        joined,seconds=materialize_union(c,baseline,[info['glob'] for _,info in standalones],'v1_plus_all')
        sources['v1_plus_all']={'glob':joined.as_posix(),'kind':'union','union_wall_seconds':seconds}
    results={}
    for name,info in sources.items():
        print('evaluating materialized policy',name,flush=True)
        results[name]=eval_one(c,name,info['glob'],baseline)
        if 'union_wall_seconds' in info: results[name]['union_materialization_wall_seconds']=info['union_wall_seconds']
    frontier=nondominated(results)
    write_json(R/'policy_evaluation.json',{'status':'MATERIALIZED_RESEARCH_FRONTIER','policies':results,
        'nondominated':frontier,'frontier_axes':['pair_candidate_recall','oracle_macro_f05','mean_candidates_per_s1','p99'],
        'research_s1':len(research),'holdout_opened':False,'target_corpus':'complete train S2/S3 in every retrieval pass'})
    lines=['# Materialized candidate research frontier','','| Policy | Candidates | Mean/S1 | P95 | P99 | Max | Pair recall | Oracle macro F0.5 | GT added | GT lost | GT/1000 added | Frontier |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for name,x in results.items():
        d=x['comparisons_vs_v1'];eff=d['gt_recovered_per_1000_added']
        lines.append(f"| {name} | {x['candidate_count']:,} | {x['mean_candidates_per_s1']:.2f} | {x['p95']:.0f} | {x['p99']:.0f} | {x['max']} | {x['pair_candidate_recall']:.6f} | {x['oracle_macro_f05']:.6f} | {d['gt_added']} | {d['gt_lost']} | {eff:.3f} | {'yes' if name in frontier else 'no'} |" if eff is not None else
                     f"| {name} | {x['candidate_count']:,} | {x['mean_candidates_per_s1']:.2f} | {x['p95']:.0f} | {x['p99']:.0f} | {x['max']} | {x['pair_candidate_recall']:.6f} | {x['oracle_macro_f05']:.6f} | {d['gt_added']} | {d['gt_lost']} | n/a | {'yes' if name in frontier else 'no'} |")
    lines += ['', 'The frontier uses four measured quality/volume axes. Runtime, process RSS and disk are reported from stage manifests separately; the user should weigh those before a research recommendation. No point is a validated V2 policy.', '']
    (R/'policy_frontier.md').write_text('\n'.join(lines),encoding='utf-8')
    log('materialized_policies_evaluated',command='.venv\\Scripts\\python.exe -B scripts\\v2_evaluate.py',
        v2_research_label_read=True,scope='v2_candidate_research only',frontier=frontier,
        metrics_sha256=sha(R/'policy_evaluation.json'))
    print(json.dumps({'frontier':frontier,'policies':{n:{'recall':x['pair_candidate_recall'],'oracle':x['oracle_macro_f05'],'mean':x['mean_candidates_per_s1']} for n,x in results.items()}},indent=2))


if __name__=='__main__':
    with Monitor(R/'tmp/policy_evaluate') as m: run()
    write_json(R/'policy_evaluation_resources.json',m.result())
