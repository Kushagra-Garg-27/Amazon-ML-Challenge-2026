"""Reports, freeze manifests, projections, errors, and experiment ledger."""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path

import duckdb
import lightgbm as lgb

from .controlled import (FEATURE_GROUPS, MODEL_FEATURE_NAMES_V1_1, connect, evaluate,
    entity_ids_for_role, load_population, sha256)

W=Path("work")


def peak(path: Path) -> int:
    if not path.exists(): return 0
    matches=re.findall(r"peak_working_set_bytes=(\d+)",path.read_text(errors="replace"))
    return int(matches[-1]) if matches else 0


def sample_tiers() -> dict:
    con=connect()
    con.execute("""CREATE TEMP TABLE gt AS SELECT source1_entity_id entity_id,trim(mid) mid
      FROM read_csv('dataset/train/train_ground_truth.tsv',delim='\t',header=true,quote='',all_varchar=true),
      unnest(string_split(matched_entity_ids,',')) u(mid)
      WHERE source1_entity_id IN (SELECT entity_id FROM read_parquet('work/model_development_samples.parquet') WHERE sample_role LIKE 'model_fit%')
      AND matched_entity_ids IS NOT NULL AND length(trim(mid))>0""")
    out={}; total_feature_seconds=150.515+183.112+143.411+178.918
    threshold_candidates=8621016
    for tier,col in (("A","tier_a"),("B","tier_b"),("C","tier_c")):
        pred=f"s.{col}"
        row=con.sql(f"""WITH e AS (SELECT entity_id,country_norm FROM read_parquet('work/model_development_samples.parquet') s WHERE {pred}),
          g AS (SELECT entity_id,count(*) truth_n FROM gt JOIN e USING(entity_id) GROUP BY 1),
          r AS (SELECT l.source1_entity_id entity_id,sum(l.y) recovered,count(*) candidates,
                       count(*) filter(where f.target_is_s2) s2,count(*) filter(where f.target_is_s3) s3,
                       count(*) filter(where f.s1_address_missing or f.target_address_missing) address_missing
                FROM read_parquet('work/model_development_labels.parquet') l
                JOIN read_parquet('work/model_development_features/model_fit*.parquet') f USING(source1_entity_id,target_entity_id)
                JOIN e ON l.source1_entity_id=e.entity_id GROUP BY 1),
          per AS (SELECT e.entity_id,e.country_norm,coalesce(g.truth_n,0) truth_n,coalesce(r.recovered,0) recovered,
                   coalesce(r.candidates,0) candidates,coalesce(r.s2,0) s2,coalesce(r.s3,0) s3,coalesce(r.address_missing,0) address_missing
                  FROM e LEFT JOIN g USING(entity_id) LEFT JOIN r USING(entity_id))
          SELECT count(*),sum(candidates),sum(recovered),sum(truth_n),
            avg(CASE WHEN truth_n=0 THEN 1 WHEN recovered=0 THEN 0 ELSE 1.25*(recovered::DOUBLE/truth_n)/(.25+recovered::DOUBLE/truth_n) END),
            avg(candidates),median(candidates),quantile_cont(candidates,.9),quantile_cont(candidates,.95),quantile_cont(candidates,.99),max(candidates),
            count(*) filter(where candidates=0),sum(s2),sum(s3),sum(address_missing),
            count(*) filter(where country_norm='india'),count(*) filter(where country_norm='us') FROM per""").fetchone()
        keys=("s1","candidates","recovered_positives","gt_links","candidate_oracle_macro_f0_5","mean_candidates","median_candidates","p90","p95","p99","max","zero_candidate_s1","s2_candidates","s3_candidates","address_missing_candidates","india_s1","us_s1")
        d=dict(zip(keys,row)); d["pair_candidate_recall"]=d["recovered_positives"]/d["gt_links"]
        d["projected_feature_bytes"]=round(d["candidates"]*33.648810889556586)
        d["candidate_generation_seconds_estimate"]=d["candidates"]*(421.318/threshold_candidates)
        d["feature_seconds_estimate"]=d["candidates"]*(total_feature_seconds/threshold_candidates)
        d["measured_candidate_peak_rss_bytes"]=950566912; d["measured_feature_peak_rss_bytes"]=1007607808
        d["measured_peak_temp_bytes"]=2287271936
        d["provenance"]={str(k):v for k,v in con.sql(f"""SELECT f.provenance,count(*) FROM read_parquet('work/model_development_features/model_fit*.parquet') f JOIN read_parquet('work/model_development_samples.parquet') s ON f.source1_entity_id=s.entity_id WHERE s.{col} GROUP BY 1 ORDER BY 1""").fetchall()}
        out[tier]=d
    con.close(); (W/"model_development_sample_tiers.json").write_text(json.dumps(out,indent=2)+"\n")
    lines=["# Development sample tiers","","Nested deterministic model-fit tiers. Time values are density-based estimates from the directly measured threshold materialization; RSS and temp are measured shared-process peaks.","", "| Tier | S1 | Candidates | Positives | Oracle F0.5 | Pair recall | Mean | P95 | P99 | Max | Feature MB | Candidate s est. | Feature s est. |","|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for k,d in out.items(): lines.append(f"| {k} | {d['s1']:,} | {d['candidates']:,} | {d['recovered_positives']:,} | {d['candidate_oracle_macro_f0_5']:.6f} | {d['pair_candidate_recall']:.6f} | {d['mean_candidates']:.2f} | {d['p95']:.0f} | {d['p99']:.0f} | {d['max']:,} | {d['projected_feature_bytes']/1e6:.1f} | {d['candidate_generation_seconds_estimate']:.1f} | {d['feature_seconds_estimate']:.1f} |")
    lines += ["","All tiers contain India/US, S2/S3, missing-address, heavy-block, and all observed provenance-bit categories. Tier D was skipped because Tier C improved baseline-dev macro F0.5 by only 0.000879 over Tier B while more than doubling sampled rows.",""]
    (W/"model_development_sample_tiers.md").write_text("\n".join(lines),encoding="utf-8")
    return out


def negative_report(results: dict) -> dict:
    samples=json.loads((W/"model_development_negative_samples.json").read_text()); con=connect(); detail={}
    for name in ("current_hybrid_tier_b","mixed_tier_b","mined_tier_b"):
        path=samples[name]["path"]
        country=dict(con.sql(f"""SELECT s.country_norm,count(*) FROM read_parquet('{path}') p JOIN read_parquet('work/model_development_samples.parquet') s ON p.source1_entity_id=s.entity_id GROUP BY 1""").fetchall())
        prov={str(k):v for k,v in con.sql(f"SELECT provenance,count(*) FROM read_parquet('{path}') WHERE y=0 GROUP BY 1 ORDER BY 1").fetchall()}
        d={**samples[name],"country":country,"negative_provenance":prov}
        if name.startswith("current"): d["hard_negatives"]=d["negatives"]; d["random_negatives"]=0; d["collision_negatives"]=0
        elif name.startswith("mixed"):
            # Exact selection-origin reconstruction with deterministic priority.
            row=con.sql(f"""WITH base AS (SELECT f.*,l.y FROM read_parquet('work/model_development_features/model_fit*.parquet') f JOIN read_parquet('work/model_development_labels.parquet') l USING(source1_entity_id,target_entity_id) JOIN read_parquet('work/model_development_samples.parquet') s ON f.source1_entity_id=s.entity_id WHERE s.tier_b),r AS (
              SELECT *,row_number() OVER(PARTITION BY source1_entity_id,y,target_is_s2 ORDER BY (retrieval_pass_count::DOUBLE*100+exact_address_norm::INT*80+exact_name_nosuffix::INT*60+exact_name_sorted::INT*40+name_token_set_ratio*20+address_token_set_ratio*20+exact_numeric_set::INT*12+exact_postal_token::INT*12) DESC,target_entity_id) hr,
              row_number() OVER(PARTITION BY source1_entity_id,y,target_is_s2 ORDER BY (exact_address_norm OR exact_name_nosuffix OR exact_name_sorted) DESC,provenance DESC,(retrieval_pass_count::DOUBLE*100+exact_address_norm::INT*80+exact_name_nosuffix::INT*60+exact_name_sorted::INT*40+name_token_set_ratio*20+address_token_set_ratio*20) DESC,target_entity_id) cr FROM base), z AS (SELECT r.* FROM r JOIN read_parquet('{path}') p USING(source1_entity_id,target_entity_id) WHERE r.y=0)
              SELECT count(*) filter(where hr<=6),count(*) filter(where hr>6 and (exact_address_norm OR exact_name_nosuffix OR exact_name_sorted OR retrieval_pass_count>=2) and cr<=3),count(*) filter(where not(hr<=6) and not((exact_address_norm OR exact_name_nosuffix OR exact_name_sorted OR retrieval_pass_count>=2) and cr<=3)) FROM z""").fetchone()
            d["hard_negatives"],d["collision_negatives"],d["random_negatives"]=row
        else:
            row=con.sql(f"""WITH base AS (SELECT f.source1_entity_id,f.target_entity_id,f.target_is_s2,l.y,s.score
              FROM read_parquet('work/model_development_features/model_fit*.parquet') f
              JOIN read_parquet('work/model_development_labels.parquet') l USING(source1_entity_id,target_entity_id)
              JOIN read_parquet('work/scores_mining_fit/*.parquet') s USING(source1_entity_id,target_entity_id)
              JOIN read_parquet('work/model_development_samples.parquet') sel ON f.source1_entity_id=sel.entity_id WHERE sel.tier_b),
              r AS (SELECT *,row_number() OVER(PARTITION BY source1_entity_id,y,target_is_s2 ORDER BY score DESC,target_entity_id) mined_rn,
                row_number() OVER(PARTITION BY source1_entity_id,y,target_is_s2 ORDER BY md5(target_entity_id||':mined-random-v1'),target_entity_id) random_rn FROM base),
              z AS (SELECT r.* FROM r JOIN read_parquet('{path}') p USING(source1_entity_id,target_entity_id) WHERE r.y=0)
              SELECT count(*) filter(where mined_rn<=10),count(*) filter(where mined_rn>10 and random_rn<=2) FROM z""").fetchone()
            d["hard_negatives"],d["random_negatives"]=row; d["collision_negatives"]=0
            d["origin_note"]="mined sample retains up to 10 scored false positives/source plus up to 2 deterministic random/source; overlap assigned to mined"
        detail[name]=d
    con.close()
    lines=["# Controlled negative-sampling comparison","","All recovered positives are retained. Metrics use all baseline-dev candidates.","","| Policy | Rows | Positives | Negatives | Neg/pos | Hard/mined | Collision | Random | S2 neg | S3 neg | India rows | US rows | SHA-256 | Macro F0.5 |","|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|"]
    metrics=results["negative_sampling"]["experiments"]
    for name,d in detail.items():
        key=name.removesuffix("_tier_b"); m=metrics[key]["best"]
        lines.append(f"| {key} | {d['rows']:,} | {d['positives']:,} | {d['negatives']:,} | {d['negatives']/d['positives']:.2f} | {d['hard_negatives']:,} | {d['collision_negatives']:,} | {d['random_negatives']:,} | {d['negative_s2']:,} | {d['negative_s3']:,} | {d['country'].get('india',0):,} | {d['country'].get('us',0):,} | `{d['sha256']}` | {m['macro_f0_5']:.6f} |")
    lines += ["",f"Selected: **{results['negative_sampling']['selected']}**. One mining round only. Training process peak for the combined experiment driver: {peak(W/'model_development_experiments_retry.log')/2**20:.1f} MiB.",""]
    (W/"model_development_negative_sampling_report.md").write_text("\n".join(lines),encoding="utf-8")
    (W/"model_development_negative_sampling_detail.json").write_text(json.dumps(detail,indent=2)+"\n")
    return detail


def error_analysis() -> dict:
    out={}; threshold=.61
    for population,score_glob,role_pred,feature_glob in (
      ("baseline_dev","work/scores_frozen_model_baseline_dev/*.parquet","sample_role LIKE 'baseline_dev%'","work/model_development_features/baseline_dev*.parquet"),
      ("model_tune","work/scores_frozen_model_tune/*.parquet","sample_role='model_tune'","work/model_development_features/model_tune*.parquet")):
        con=connect(); con.execute(f"CREATE TEMP TABLE entities AS SELECT entity_id,country_norm FROM read_parquet('work/model_development_samples.parquet') WHERE {role_pred}")
        con.execute("""CREATE TEMP TABLE gt AS SELECT source1_entity_id entity_id,trim(mid) mid FROM read_csv('dataset/train/train_ground_truth.tsv',delim='\t',header=true,quote='',all_varchar=true),unnest(string_split(matched_entity_ids,',')) u(mid) WHERE source1_entity_id IN (SELECT entity_id FROM entities) AND matched_entity_ids IS NOT NULL AND length(trim(mid))>0""")
        con.execute(f"""CREATE TEMP VIEW pairs AS SELECT s.*,l.y,f.provenance,f.source_balanced_rank,
          (f.s1_address_missing OR f.target_address_missing) address_missing,f.script_conflict,
          (f.exact_name_norm OR f.exact_name_sorted OR f.exact_name_nosuffix) exact_name,
          f.exact_address_norm,(f.conflicting_address_numbers OR f.conflicting_postal_tokens) numeric_conflict,
          (s.score>={threshold}) predicted FROM read_parquet('{score_glob}') s JOIN read_parquet('work/model_development_labels.parquet') l USING(source1_entity_id,target_entity_id) JOIN read_parquet('{feature_glob}') f USING(source1_entity_id,target_entity_id)""")
        total_gt=con.sql("SELECT count(*) FROM gt").fetchone()[0]; recovered=con.sql("SELECT sum(y) FROM pairs").fetchone()[0]
        counts=con.sql("SELECT count(*) filter(where y=1 and not predicted),count(*) filter(where y=0 and predicted) FROM pairs").fetchone()
        con.execute("""CREATE TEMP VIEW per AS WITH t AS (SELECT entity_id,count(*) truth_n FROM gt GROUP BY 1),p AS (SELECT source1_entity_id entity_id,count(*) filter(where predicted) pred_n,count(*) filter(where predicted and y=1) tp FROM pairs GROUP BY 1) SELECT e.entity_id,e.country_norm,coalesce(t.truth_n,0) truth_n,coalesce(p.pred_n,0) pred_n,coalesce(p.tp,0) tp FROM entities e LEFT JOIN t USING(entity_id) LEFT JOIN p USING(entity_id)""")
        sets=con.sql("""SELECT count(*) filter(where truth_n=0 and pred_n>0),count(*) filter(where truth_n>0 and tp=truth_n),count(*) filter(where truth_n>1 and tp>0 and tp<truth_n),count(*) filter(where pred_n>truth_n),count(*) filter(where truth_n>0 and pred_n=0) FROM per""").fetchone()
        d={"s1":con.sql("select count(*) from entities").fetchone()[0],"gt_links":total_gt,"candidate_generation_loss":total_gt-recovered,"matcher_false_negative":counts[0],"matcher_false_positive":counts[1],"set_level":dict(zip(("singleton_false_positive_s1","complete_recovery_s1","partial_multi_match_s1","overprediction_s1","empty_prediction_error_s1"),sets))}
        dimensions={
          "country":"e.country_norm","source":"CASE WHEN starts_with(p.target_entity_id,'S2-') THEN 'S2' ELSE 'S3' END",
          "provenance":"cast(p.provenance as varchar)","rank_band":"CASE WHEN p.source_balanced_rank=0 THEN '0' WHEN p.source_balanced_rank<=10 THEN '1-10' WHEN p.source_balanced_rank<=25 THEN '11-25' ELSE '26-50' END",
          "address_missing":"cast(p.address_missing as varchar)","script_conflict":"cast(p.script_conflict as varchar)",
          "exact_evidence":"CASE WHEN p.exact_address_norm THEN 'exact_address' WHEN p.exact_name THEN 'exact_name' ELSE 'neither' END",
          "numeric_conflict":"cast(p.numeric_conflict as varchar)"}
        d["pair_breakdown"]={}
        for key,expr in dimensions.items():
            rows=con.sql(f"SELECT {expr} k,count(*) filter(where p.y=1 and not p.predicted) matcher_fn,count(*) filter(where p.y=0 and p.predicted) fp FROM pairs p JOIN entities e ON p.source1_entity_id=e.entity_id GROUP BY 1 ORDER BY 1").fetchall()
            d["pair_breakdown"][key]={str(k):{"matcher_fn":fn,"fp":fp} for k,fn,fp in rows}
        d["true_link_count"]={str(k):{"s1":n,"complete":complete,"partial":partial,"empty":empty} for k,n,complete,partial,empty in con.sql("SELECT truth_n,count(*),count(*) filter(where truth_n>0 and tp=truth_n),count(*) filter(where truth_n>1 and tp>0 and tp<truth_n),count(*) filter(where truth_n>0 and pred_n=0) FROM per GROUP BY 1 ORDER BY 1").fetchall()}
        out[population]=d; con.close()
    (W/"final_matcher_error_analysis.json").write_text(json.dumps(out,indent=2)+"\n")
    lines=["# Frozen matcher error analysis","",f"Threshold: `{threshold}`. Candidate-generation losses and matcher errors are disjoint.",""]
    for name,d in out.items():
        lines += [f"## {name}","",f"- Candidate-generation loss: {d['candidate_generation_loss']:,}",f"- Matcher false negatives among recovered candidates: {d['matcher_false_negative']:,}",f"- Matcher false positives: {d['matcher_false_positive']:,}"]+[f"- {k.replace('_',' ')}: {v:,}" for k,v in d['set_level'].items()]+["", "Detailed country, source, provenance, rank, address, script, exact-evidence, numeric-conflict, and true-link-count aggregates are in `work/final_matcher_error_analysis.json`.",""]
    (W/"final_matcher_error_analysis.md").write_text("\n".join(lines),encoding="utf-8")
    return out


def freeze(results: dict, threshold: dict, tiers: dict, errors: dict) -> dict:
    f=results["freeze_before_threshold"]; selected=threshold["selected_result"]
    policy={"schema_version":1,"status":"development_freeze_not_final_evaluation",
      "candidate_policy_sha256":f["candidate_policy_sha256"],"feature_spec_version":"feature_spec_v1.1",
      "feature_spec_sha256":f["feature_spec_sha256"],"feature_order":f["features"],
      "feature_types":["float32"]*len(f["features"]),"removed_redundant_inputs":["retrieved_sorted_name","retrieved_exact_address","shared_postal_tokens","target_is_s3"],
      "training_split":"model_fit tier B","training_split_manifest_sha256":f["development_split_sha256"],
      "negative_sampling":{"policy":"one_round_mined","sample_sha256":f["training_sample_sha256"],"all_recovered_positives":True,"mined_false_positives_per_source_s1":10,"random_per_source_s1":2,"rounds":1},
      "lightgbm":{"version":f["lightgbm_version"],"parameters":{**f["params"],"objective":"binary","metric":"binary_logloss","seed":42,"feature_fraction_seed":42,"bagging_seed":42,"data_random_seed":42,"deterministic":True,"force_col_wise":True},"rounds":f["rounds"],"model_format":"LightGBM native text"},
      "threshold":selected["threshold"],"threshold_selection":"one coarse grid at 0.05 plus one 0.01 fine grid; maximize macro F0.5; lower threshold wins exact ties",
      "set_assembly":{"policy":"global_threshold","comparison":">=","allow_empty":True,"allow_multiple":True,"force_top_1":False,"max_matches":None,"numeric_conflict_filter":False,"source_specific_thresholds":False,"deduplicate_identity":True,"order":"source1_entity_id,target_entity_id"},
      "inference":{"batch_candidates":200000,"threads":2,"matrix_dtype":"float32","identity_columns_excluded_from_matrix":True},
      "seeds":{"development_split":"model_development_v1_20260926","sample":"model_development_samples_v1_20260926","lightgbm":42}}
    (W/"final_matcher_policy.json").write_text(json.dumps(policy,indent=2)+"\n")
    artifacts=["final_matcher_model.txt","final_matcher_policy.json","feature_spec_v1_1.json","model_development_split_manifest.parquet","model_development_negative_samples/mined_tier_b.parquet","final_matcher_threshold_curve.parquet","matcher_reproducibility.json"]
    manifest={"schema_version":1,"status":"development_freeze_not_final_evaluation","created_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"artifacts":[]}
    for rel in artifacts:
        p=W/rel; manifest["artifacts"].append({"path":p.as_posix(),"bytes":p.stat().st_size,"sha256":sha256(p)})
    manifest["model_final_eval_accessed"]=False; manifest["grouped_stress_evaluated"]=False; manifest["test_inference_run"]=False
    (W/"final_matcher_manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    policy_sha=sha256(W/"final_matcher_policy.json"); manifest_sha=sha256(W/"final_matcher_manifest.json")

    n=271582123; bpc=289960971/8621016; score_bpc=102078146/8621016; feat_sec=656/8621016; infer_sec=threshold["threshold_scoring"]["seconds"]/8621016; cand_sec=421.318/8621016
    projection={"chosen_training_sample":{"rows":f['model']['rows'],"matrix_bytes":f['model']['matrix_bytes'],"sample_bytes":(W/f['training_sample'].removeprefix('work/')).stat().st_size,"training_seconds":f['model']['training_seconds']},
      "model_threshold_observed":{"s1":55091,"candidates":8621016,"feature_bytes":289960971,"score_bytes":102078146,"candidate_seconds":421.318,"feature_seconds":656.0,"inference_seconds":threshold['threshold_scoring']['seconds'],"peak_rss_bytes":peak(W/'model_threshold_run.log')},
      "model_final_eval_projected":{"s1":110341,"candidates":round(8621016*110341/55091),"feature_bytes":round(289960971*110341/55091),"score_bytes":round(102078146*110341/55091),"wall_seconds":round((421.318+656+threshold['threshold_scoring']['seconds'])*110341/55091)},
      "test_projected":{"s1":1732544,"candidates":n,"candidate_identity_provenance_bytes":round(n*(sum(p.stat().st_size for p in (W/'model_threshold_candidates').glob('*.parquet'))/8621016)),"feature_bytes":round(n*bpc),"score_bytes":round(n*score_bpc),"candidate_hours":n*cand_sec/3600,"feature_hours":n*feat_sec/3600,"inference_minutes":n*infer_sec/60,"output_grouping_minutes":n*infer_sec/60*.25},
      "in_memory":{"batch_candidates":200000,"float32_matrix_bytes":200000*len(f['features'])*4,"score_bytes":200000*4,"expected_process_peak_bytes":1800000000},"restart_partitions":{"train":128,"final_eval":64,"test":96},"temp_disk_recommendation_gb":{"final_eval":8,"test":48},"model_load_overhead":"below one second; 645 KB native text model"}
    (W/"final_matcher_resource_projection.json").write_text(json.dumps(projection,indent=2)+"\n")

    ab=results['ablations']['experiments']; lc=results['learning_curve']['experiments']; cfg=results['configuration_comparison']['experiments']; neg=results['negative_sampling']['experiments']
    lines=["# Final matcher development training report","","> Development freeze only. `model_final_eval` has not been evaluated.","","## Current-state and implementation audit","","Frozen candidate policy, feature-v1, and top-level split checksums matched. Initial tests: 102 passed in 27.291s, exit 0. Free RAM was 3.911 GiB and free disk was 216.687 GiB; no Python/DuckDB workload was active.","","The NumPy logistic baseline optimizes class-weighted binary cross-entropy with L2 on coefficients, z-score scaling, clipped logits, seeded shuffled mini-batches, and fixed 18 epochs. It has no convergence-based stop. Coefficients, intercept, means, scales, feature order, and seed are plain JSON.","","The LightGBM smoke used 65 float32 numeric inputs, no categorical declaration or class weights, native missing handling, fixed seed 42, two threads, separate pilot train/calibration populations, and a whole-process peak covering load, Dataset construction, training, and prediction.","","## Development firewall","",(W/'model_development_split_report.md').read_text(),"## Feature audit","",(W/'feature_audit_v1.md').read_text(),"## Negative sampling","",(W/'model_development_negative_sampling_report.md').read_text(),"## Feature ablations","","| View | Features | Macro F0.5 | Recovered F0.5 | Precision | Recall | India | US | S2 recall | S3 recall | Train s | Infer s |","|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for k,v in ab.items():
        b=v['best']; lines.append(f"| {k} | {v['feature_count']} | {b['macro_f0_5']:.6f} | {b['recovered_truth_macro_f0_5']:.6f} | {b['pair_precision']:.6f} | {b['pair_recall']:.6f} | {b['india_macro_f0_5']:.6f} | {b['us_macro_f0_5']:.6f} | {b['s2_recall']:.6f} | {b['s3_recall']:.6f} | {v['train']['training_seconds']:.2f} | {v['inference']['seconds']:.2f} |")
    lines += ["",f"Selected: **{results['ablations']['selected']}**.","","## Learning curve","","| Tier | Rows | Positives | Model train s | Macro F0.5 | Precision | Recall | Improvement |","|---|---:|---:|---:|---:|---:|---:|---:|"]
    prev=None
    for k,v in lc.items():
        b=v['best']; imp='' if prev is None else f"{b['macro_f0_5']-prev:+.6f}"; prev=b['macro_f0_5']; lines.append(f"| {k.upper()} | {v['train']['rows']:,} | {v['train']['positives']:,} | {v['train']['training_seconds']:.2f} | {b['macro_f0_5']:.6f} | {b['pair_precision']:.6f} | {b['pair_recall']:.6f} | {imp} |")
    lines += ["",f"Selected Tier **{results['learning_curve']['selected_tier'].upper()}**. Tier C improved only 0.000879 over Tier B, so full 1.76M-S1 fitting is not justified.","","## Limited configuration comparison","","| Configuration | Leaves | Depth | Rounds | Macro F0.5 | Precision | Recall | Train s | Infer s |","|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for k,v in cfg.items():
        b=v['best']; lines.append(f"| {k} | {v['params']['num_leaves']} | {v['params']['max_depth']} | {v['train']['best_iteration']} | {b['macro_f0_5']:.6f} | {b['pair_precision']:.6f} | {b['pair_recall']:.6f} | {v['train']['training_seconds']:.2f} | {v['inference']['seconds']:.2f} |")
    lines += ["",f"Selected: **{results['configuration_comparison']['selected']}**.","","## Memory stages","",f"- Candidate generation peak: 906.5 MiB; DuckDB 700 MiB, one thread.",f"- Feature generation peak: 960.9 MiB; 20,000-row Arrow batches.",f"- Combined development experiments peak: {peak(W/'model_development_experiments_retry.log')/2**20:.1f} MiB.",f"- Threshold scoring/search peak: {peak(W/'model_threshold_run.log')/2**20:.1f} MiB.",f"- Selected sample matrix: {f['model']['matrix_bytes']/2**20:.1f} MiB; Dataset construction {f['model']['dataset_seconds']:.2f}s; training {f['model']['training_seconds']:.2f}s.","","## Frozen configuration","","```json",json.dumps(policy,indent=2),"```","","## One-time threshold selection","",f"Candidate oracle macro F0.5: **{threshold['candidate_oracle']['macro_f0_5']:.6f}**; pair ceiling: **{threshold['candidate_oracle']['pair_recall']:.6f}**.",f"Selected global threshold **{selected['threshold']:.2f}**: macro F0.5 **{selected['macro_f0_5']:.6f}**, recovered-truth F0.5 **{selected['recovered_truth_macro_f0_5']:.6f}**, precision **{selected['pair_precision']:.6f}**, recall **{selected['pair_recall']:.6f}**, singleton accuracy **{selected['singleton_accuracy']:.6f}**, mean predictions **{selected['avg_predicted']:.3f}**, empty rate **{selected['empty_prediction_rate']:.6f}**.",f"India/US macro: {selected['india_macro_f0_5']:.6f}/{selected['us_macro_f0_5']:.6f}; S2/S3 recall: {selected['s2_recall']:.6f}/{selected['s3_recall']:.6f}; address present/missing recall: {selected['address_present_recall']:.6f}/{selected['address_missing_recall']:.6f}.",f"Tune-only S2/S3 optimal thresholds were both 0.70; source-specific thresholds were ineligible. Numeric-conflict rejection scored {threshold['policies']['global_reject_numeric_conflict']['best']['macro_f0_5']:.6f} and was rejected.","","## Reproducibility","","Representative 300,238-row feature checksum matched exactly. Two fresh model loads had max score difference 0.0, identical decisions and prediction sets, and predictions were a subset of candidates.","","## Full-scale projection","","```json",json.dumps(projection,indent=2),"```",""]
    (W/"final_matcher_training_report.md").write_text("\n".join(lines),encoding="utf-8")
    return {"policy_sha256":policy_sha,"manifest_sha256":manifest_sha,"projection":projection}


def ledger(results: dict, threshold: dict) -> None:
    now=datetime.datetime.now(datetime.timezone.utc).isoformat(); base={"timestamp":now,"candidate_policy_sha256":sha256(W/'final_candidate_policy.json'),"feature_spec_sha256":sha256(W/'feature_spec_v1_1.json'),"development_split_sha256":sha256(W/'model_development_split_manifest.parquet')}
    rows=[{**base,"run_id":"controlled_candidates_model_tune_us_700mb","status":"failed","failure_reason":"DuckDB final-sort allocation exceeded 700 MB; successful shards retained","artifact_paths":["work/model_development_candidate_run.log"]},{**base,"run_id":"controlled_candidates_model_tune_us_800mb","status":"failed","failure_reason":"DuckDB requested contiguous 128 MB block under 800 MB cap","artifact_paths":["work/model_development_candidate_retry.log"]},{**base,"run_id":"controlled_lgb_seed_construct","status":"failed","failure_reason":"data_random_seed supplied after Dataset construction; regression fixed","artifact_paths":["work/model_development_experiments.log"]}]
    for section in ('negative_sampling','ablations','learning_curve','configuration_comparison'):
        for name,v in results[section]['experiments'].items():
            rows.append({**base,"run_id":f"controlled_{section}_{name}","status":"completed","sample_sha256":v.get('sample',{}).get('sha256',results['freeze_before_threshold']['training_sample_sha256']),"model_parameters":v.get('params',{}),"threshold":v['best']['threshold'],"metrics":v['best'],"runtime_seconds":{"train":v['train']['training_seconds'],"inference":v['inference']['seconds']},"peak_rss_bytes":peak(W/'model_development_experiments_retry.log'),"artifact_paths":[]})
    for name,v in threshold['policies'].items(): rows.append({**base,"run_id":f"controlled_threshold_{name}","status":"completed","threshold":v['best']['threshold'],"metrics":v['best'],"peak_rss_bytes":peak(W/'model_threshold_run.log'),"artifact_paths":["work/final_matcher_threshold_curve.parquet"]})
    with (W/"model_experiments.jsonl").open("a",encoding="utf-8") as f:
        for row in rows: f.write(json.dumps(row,separators=(',',':'))+'\n')


def run() -> None:
    results=json.loads((W/"model_development_results.json").read_text()); threshold=json.loads((W/"model_threshold_results.json").read_text())
    tiers=sample_tiers(); negative_report(results); errors=error_analysis(); frozen=freeze(results,threshold,tiers,errors); ledger(results,threshold)
    print(json.dumps(frozen,indent=2))


if __name__=="__main__": run()
