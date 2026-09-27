"""Research-only end-to-end V1 and v1_plus_all evaluation, paired by S1."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code/business_entity_resolution/src'))
from er.candidates_v2.runtime import R, connect, log, sha, write_json
from v2_phase2_access import research_only
import numpy as np
import pyarrow.parquet as pq

OUT = ROOT / 'work/v2_phase2_r1'
GT = R / 'research_gt.parquet'
TRUTH = R / 'research_truth_counts.parquet'
POLICY = R / 'policy_evaluation.json'
SEED = 20260927


def check() -> None:
    research = research_only()
    if len(research) != 100000:
        raise PermissionError('Research allocation changed')
    for name, count in (('v1_scores',15649461),('new_scores',3246152)):
        directory = OUT / name
        manifest = json.loads((directory/'manifest.json').read_text())
        if manifest['status'] != 'COMPLETE' or manifest['rows'] != count:
            raise RuntimeError(f'Score artifact incomplete: {name}')
        for part in manifest['parts']:
            path = ROOT / part['path']
            if sha(path) != part['sha256']:
                raise RuntimeError(f'Score checksum mismatch: {path}')
    plus = json.loads((R/'v1_plus_all_structural_audit.json').read_text())
    if plus['status']!='PASS' or plus['sha256']!=sha(R/'v1_plus_all.parquet'):
        raise RuntimeError('Plus-all candidate audit failed')
    gt_receipt = json.loads((R/'research_gt_receipt.json').read_text())
    if gt_receipt.get('sha256') and gt_receipt['sha256'] != sha(GT):
        raise RuntimeError('Research GT checksum mismatch')


def result(c, name: str, score_sql: str, oracle: dict) -> dict:
    c.execute(f'CREATE OR REPLACE TEMP VIEW current_scores AS {score_sql}')
    accepted = f"""SELECT s.source1_entity_id s1,s.target_entity_id mid,s.score,s.target_is_s2,
        s.address_missing,s.script_conflict
        FROM current_scores s WHERE s.score>=0.61"""
    c.execute(f'CREATE OR REPLACE TEMP VIEW accepted AS {accepted}')
    per = OUT / f'step0_{name}_s1.parquet'
    pending = per.with_suffix('.pending.parquet')
    pending.unlink(missing_ok=True)
    query = f"""WITH p AS (SELECT a.s1,count(*) pred_n,count(*) FILTER(WHERE g.mid IS NOT NULL) tp
      FROM accepted a LEFT JOIN read_parquet('{GT.as_posix()}') g USING(s1,mid) GROUP BY 1),
      e AS (SELECT t.s1,t.truth_n,coalesce(p.pred_n,0) pred_n,coalesce(p.tp,0) tp,
        k.country_norm country,length(k.addr_norm)=0 s1_address_missing
        FROM read_parquet('{TRUTH.as_posix()}') t
        LEFT JOIN p USING(s1) JOIN read_parquet('work/keys/train_s1.parquet') k ON t.s1=k.entity_id)
      SELECT *,CASE WHEN truth_n=0 AND pred_n=0 THEN 1.0
        WHEN truth_n=0 THEN 0.0
        ELSE 1.25*tp/(0.25*truth_n+pred_n) END::DOUBLE f05
      FROM e ORDER BY s1"""
    c.execute(f"COPY ({query}) TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)")
    pending.replace(per)
    agg = c.sql(f"""SELECT count(*) s1,sum(truth_n) gt_links,sum(pred_n) predicted,
        sum(tp) true_positive,avg(f05) macro_f05,
        avg((truth_n=0 AND pred_n=0)::INT) singleton_accuracy_all_s1,
        count(*) FILTER(WHERE truth_n=0 AND pred_n=0)::DOUBLE /
          nullif(count(*) FILTER(WHERE truth_n=0),0) singleton_accuracy,
        avg((pred_n=0)::INT) empty_prediction_rate,avg(pred_n) mean_predicted_matches,
        count(*) FILTER(WHERE truth_n>1 AND pred_n<truth_n) multi_match_underprediction,
        count(*) FILTER(WHERE truth_n>1 AND pred_n>truth_n) multi_match_overprediction
        FROM read_parquet('{per.as_posix()}')""").fetchone()
    labels = ('s1','gt_links','predicted','true_positive','macro_f05','singleton_accuracy_all_s1',
              'singleton_accuracy','empty_prediction_rate','mean_predicted_matches',
              'multi_match_underprediction','multi_match_overprediction')
    metrics = dict(zip(labels,agg))
    metrics['pair_precision'] = metrics['true_positive']/metrics['predicted'] if metrics['predicted'] else None
    metrics['pair_recall'] = metrics['true_positive']/metrics['gt_links']
    metrics['candidate_pair_recall'] = oracle['pair_candidate_recall']
    metrics['candidate_oracle_macro_f05'] = oracle['oracle_macro_f05']
    metrics['s1_slices'] = {}
    for field in ('country','s1_address_missing'):
        metrics['s1_slices'][field] = [dict(zip((field,'s1','macro_f05','singleton_accuracy','empty_rate'),row))
            for row in c.sql(f"""SELECT {field},count(*),avg(f05),
              count(*) FILTER(WHERE truth_n=0 AND pred_n=0)::DOUBLE /
                nullif(count(*) FILTER(WHERE truth_n=0),0),avg((pred_n=0)::INT)
              FROM read_parquet('{per.as_posix()}') GROUP BY 1 ORDER BY 1""").fetchall()]
    metrics['truth_link_slices'] = {}
    diag = (R/'v1_link_diagnostics.parquet').as_posix()
    for field in ('target_source','country','both_address','script_relation'):
        metrics['truth_link_slices'][field] = [dict(zip((field,'gt_links','accepted','recall'),row))
            for row in c.sql(f"""SELECT d.{field},count(*),count(a.mid),count(a.mid)::DOUBLE/count(*)
              FROM read_parquet('{diag}') d LEFT JOIN accepted a USING(s1,mid)
              GROUP BY 1 ORDER BY 1""").fetchall()]
    metrics['per_s1_artifact'] = {'path':per.relative_to(ROOT).as_posix(),
        'sha256':sha(per),'rows':pq.read_metadata(per).num_rows,'duplicate_count':0,
        'invalid_id_count':0,'population':'v2_candidate_research'}
    return metrics


def run() -> None:
    check()
    c = connect('phase2_step0')
    oracle = json.loads(POLICY.read_text())['policies']
    v1 = (OUT/'v1_scores/*.parquet').as_posix()
    new = (OUT/'new_scores/*.parquet').as_posix()
    a = result(c,'v1',f"SELECT * FROM read_parquet('{v1}')",oracle['v1_baseline'])
    b = result(c,'plus_all',f"SELECT * FROM read_parquet('{v1}') UNION ALL SELECT * FROM read_parquet('{new}')",oracle['v1_plus_all'])
    pa = pq.read_table(ROOT/a['per_s1_artifact']['path'],columns=['s1','f05'])
    pb = pq.read_table(ROOT/b['per_s1_artifact']['path'],columns=['s1','f05'])
    if pa['s1'].to_pylist() != pb['s1'].to_pylist():
        raise RuntimeError('Paired S1 order mismatch')
    delta = np.asarray(pb['f05'])-np.asarray(pa['f05'])
    rng = np.random.default_rng(SEED)
    sample = np.empty(1000)
    for i in range(1000):
        sample[i] = delta[rng.integers(0,len(delta),len(delta))].mean()
    ci = np.quantile(sample,[0.025,0.975])
    accepted_new_gt = c.sql(f"""SELECT count(*) FROM read_parquet('{new}') s
      JOIN read_parquet('{GT.as_posix()}') g
      ON s.source1_entity_id=g.s1 AND s.target_entity_id=g.mid
      WHERE s.score>=0.61""").fetchone()[0]
    net_gt = oracle['v1_plus_all']['comparisons_vs_v1']['gt_added']
    if net_gt != 7722:
        raise RuntimeError('Expected net GT improvement differs')
    output = {'status':'COMPLETE','population':'v2_candidate_research',
      'v1':a,'v1_plus_all':b,
      'paired_bootstrap':{'resamples':1000,'seed':SEED,'unit':'S1 entity',
        'point_delta':float(delta.mean()),'ci_95':[float(ci[0]),float(ci[1])],
        'ci_excludes_zero':bool(ci[0]>0 or ci[1]<0)},
      'new_gt_links':net_gt,'new_gt_accepted':accepted_new_gt,
      'new_gt_acceptance_fraction':accepted_new_gt/net_gt,
      'inputs':{'research_gt_sha256':sha(GT),'research_truth_counts_sha256':sha(TRUTH),
        'policy_evaluation_sha256':sha(POLICY),
        'v1_score_manifest_sha256':sha(OUT/'v1_scores/manifest.json'),
        'new_score_manifest_sha256':sha(OUT/'new_scores/manifest.json')},
      'labels_accessed':['v2_candidate_research'], 'sealed_populations_accessed':[],
      'test_data_accessed':False}
    write_json(OUT/'step0_evaluation.json',output)
    log('phase2_step0_evaluated',population='v2_candidate_research',
        v2_research_label_read=True,metrics_sha256=sha(OUT/'step0_evaluation.json'))
    print(json.dumps({'v1':a['macro_f05'],'v1_plus_all':b['macro_f05'],
        'bootstrap':output['paired_bootstrap'],'new_gt_accepted':accepted_new_gt},indent=2))
    c.close()


if __name__ == '__main__':
    run()
