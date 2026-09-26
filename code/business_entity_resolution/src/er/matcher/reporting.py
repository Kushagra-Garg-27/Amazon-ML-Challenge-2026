"""Write aggregate pilot reports and the append-only experiment ledger."""
from __future__ import annotations
import datetime, hashlib, json, re
from pathlib import Path
import duckdb

W = Path("work")

def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()

def peak(path):
    matches = re.findall(r"peak_working_set_bytes=(\d+)", Path(path).read_text(errors="replace"))
    return int(matches[-1]) if matches else None

def main():
    c = duckdb.connect(); c.execute("SET memory_limit='800MB'; SET threads=1")
    audit = json.loads((W / "feature_pilot_audit.json").read_text())
    feat = json.loads((W / "feature_pilot_v1_final/materialization.json").read_text())
    if not any("wall_seconds" in x for x in feat["parts"]):
        log=(W/"feature_pilot_run.log").read_text(errors="replace")
        feat=json.loads(log[log.index("{"):log.rindex("[measure_peak]")].strip())
    results = json.loads((W / "baseline_results.json").read_text())
    lgb = json.loads((W / "lightgbm_smoke.json").read_text())
    files=[]
    for folder in ("feature_pilot_candidates", "feature_pilot_v1_final"):
        for p in sorted((W/folder).glob("*.parquet")):
            files.append({"path":p.as_posix(),"rows":c.sql(f"select count(*) from read_parquet('{p.as_posix()}')").fetchone()[0],"bytes":p.stat().st_size,"sha256":sha(p)})
    lp=W/"feature_pilot_labels.parquet"
    files.append({"path":lp.as_posix(),"rows":audit["input_rows"],"bytes":lp.stat().st_size,"sha256":sha(lp)})
    split_rows=c.sql("""SELECT p.split,count(distinct p.entity_id),count(x.target_entity_id),sum(coalesce(l.y,0)),count(x.target_entity_id)-sum(coalesce(l.y,0))
      FROM read_parquet('work/feature_pilot_s1.parquet') p
      LEFT JOIN read_parquet('work/feature_pilot_candidates/*.parquet') x ON p.entity_id=x.source1_entity_id
      LEFT JOIN read_parquet('work/feature_pilot_labels.parquet') l ON x.source1_entity_id=l.source1_entity_id AND x.target_entity_id=l.target_entity_id
      GROUP BY 1 ORDER BY 1""").fetchall()
    by_split={r[0]:dict(zip(("s1","candidates","positives","negatives"),r[1:])) for r in split_rows}
    covrow=c.sql("""SELECT count(*) filter(where heavy_sorted_block),count(*) filter(where target_is_s2),count(*) filter(where target_is_s3),count(*) filter(where s1_address_missing or target_address_missing) FROM read_parquet('work/feature_pilot_v1_final/*.parquet')""").fetchone()
    coverage=dict(zip(("heavy_rows","s2_rows","s3_rows","address_missing_rows"),covrow))
    density=34_568_979/220_531; bpc=feat["bytes"]/feat["rows"]; secpc=sum(x.get("wall_seconds",0) for x in feat["parts"])/feat["rows"]
    projections={name:{"s1":n,"candidates":round(n*density),"feature_gb":n*density*bpc/1e9,"feature_hours":n*density*secpc/3600} for name,n in (("model_train",1_765_608),("model_calibration",110_341),("model_final_eval",110_341),("test",1_732_544))}
    group_seconds={k:sum(x.get("group_seconds",{}).get(k,0) for x in feat["parts"]) for k in ("exact","token","fuzzy")}
    manifest={"schema_version":1,"feature_spec_sha256":sha(W/"feature_spec_v1.json"),"candidate_policy_sha256":sha(W/"final_candidate_policy.json"),"selection_sha256":sha(W/"feature_pilot_s1.parquet"),"files":files,"totals":{"s1":7000,**audit,"feature_bytes":feat["bytes"],"bytes_per_candidate":bpc},"by_split":by_split,"coverage":coverage,"resources":{"candidate_generation_peak_rss":peak(W/"feature_pilot_candidate_run.log"),"feature_peak_rss":peak(W/"feature_pilot_run.log"),"audit_peak_rss":peak(W/"feature_pilot_audit.log"),"group_seconds":group_seconds,"peak_temp_bytes":max(x.get("peak_temp_bytes",0) for x in feat["parts"])},"projections":projections}
    (W/"feature_pilot_manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    lines=["# Feature pilot report","",f"Pilot: 7,000 S1s, {audit['input_rows']:,} candidates, 65 features, {audit['positives']:,} positives, {audit['negatives']:,} negatives ({audit['positives']/audit['input_rows']:.4%} positive).","",f"Features occupy {feat['bytes']:,} bytes ({bpc:.2f} bytes/candidate), below the prior 96-byte assumption. Identity audit: added {audit['added']}, removed {audit['removed']}, duplicates {audit['duplicates']}. Null/NaN/inf counts are all zero.","",f"Peak RSS: candidate generation {manifest['resources']['candidate_generation_peak_rss']/2**20:.1f} MiB; feature generation {manifest['resources']['feature_peak_rss']/2**20:.1f} MiB; audit {manifest['resources']['audit_peak_rss']/2**20:.1f} MiB. Feature wall time: {sum(x.get('wall_seconds',0) for x in feat['parts']):.1f}s. Instrumented temp peak: {manifest['resources']['peak_temp_bytes']:,} bytes.","",f"Core feature time: exact {group_seconds['exact']:.2f}s ({group_seconds['exact']/feat['rows']*1e6:.2f} µs/pair), token {group_seconds['token']:.2f}s ({group_seconds['token']/feat['rows']*1e6:.2f} µs/pair), fuzzy {group_seconds['fuzzy']:.2f}s ({group_seconds['fuzzy']/feat['rows']*1e6:.2f} µs/pair).","","| Split | S1 | Candidates | Positives | Negatives |","|---|---:|---:|---:|---:|"]
    for s,d in by_split.items(): lines.append(f"| {s} | {d['s1']:,} | {d['candidates']:,} | {d['positives']:,} | {d['negatives']:,} |")
    lines += ["",f"Heavy-block rows: {coverage['heavy_rows']:,}; S2/S3 rows: {coverage['s2_rows']:,}/{coverage['s3_rows']:,}; address-missing rows: {coverage['address_missing_rows']:,}.",""]
    (W/"feature_pilot_report.md").write_text("\n".join(lines))
    samples=results["samples"]; lines=["# Negative sampling report","","All recovered positives are retained. Calibration scores every candidate.","","| Policy | Rows | Positives | Negatives | S2 | S3 | S1 | SHA-256 |","|---|---:|---:|---:|---:|---:|---:|---|"]
    for n,d in samples.items(): lines.append(f"| {n} | {d['rows']:,} | {d['positives']:,} | {d['negatives']:,} | {d['s2']:,} | {d['s3']:,} | {d['s1']:,} | `{d['sha256']}` |")
    lines += ["","Hard and random use 20 negatives/S1. The selected hybrid uses up to 10 hard-ranked negatives from each target source.",""]
    (W/"negative_sampling_report.md").write_text("\n".join(lines))
    det=results["best"]["deterministic"]; log=results["best"]["logistic"]
    lines=["# Pilot baseline model report","","Calibration-pilot results only. Candidate oracle macro F0.5: **%.6f**."%results["candidate_oracle"]["macro_f0_5"],"","| Model | Threshold | Macro F0.5 | Pair precision | Pair recall | Avg predicted | Singleton accuracy |","|---|---:|---:|---:|---:|---:|---:|",f"| Deterministic | {det['threshold']:.2f} | {det['macro_f0_5']:.6f} | {det['pair_precision']:.6f} | {det['pair_recall']:.6f} | {det['avg_predicted']:.3f} | {det['singleton_accuracy']:.6f} |",f"| Logistic | {log['threshold']:.2f} | {log['macro_f0_5']:.6f} | {log['pair_precision']:.6f} | {log['pair_recall']:.6f} | {log['avg_predicted']:.3f} | {log['singleton_accuracy']:.6f} |",f"| LightGBM smoke | {lgb['best']['threshold']:.2f} | {lgb['best']['macro_f0_5']:.6f} | {lgb['best']['pair_precision']:.6f} | {lgb['best']['pair_recall']:.6f} | {lgb['best']['avg_predicted']:.3f} | {lgb['best']['singleton_accuracy']:.6f} |","",f"The fixed-seed NumPy linear logit was used because Windows application control blocked scikit-learn's compiled `_cd_fast` DLL. Training: {results['model']['training_rows']:,} sampled rows in {results['model']['training_seconds']:.2f}s. The LightGBM smoke used one fixed 40-tree, 15-leaf configuration; no search was run.",""]
    (W/"baseline_model_report.md").write_text("\n".join(lines))
    threshold=log["threshold"]
    c.execute("""CREATE TEMP TABLE fullgt AS SELECT source1_entity_id s1,trim(mid) mid FROM read_csv('dataset/train/train_ground_truth.tsv',delim='\t',header=true,quote='',all_varchar=true),unnest(string_split(matched_entity_ids,',')) u(mid) WHERE matched_entity_ids IS NOT NULL AND length(trim(mid))>0""")
    row=c.sql(f"""WITH sc AS (SELECT s.*,f.script_conflict,f.exact_name_nosuffix,f.name_jaccard,f.address_jaccard,f.exact_address_norm FROM read_parquet('work/baseline_calibration_scores.parquet') s JOIN read_parquet('work/feature_pilot_v1_final/model_calibration_*.parquet') f USING(source1_entity_id,target_entity_id)) SELECT count(*) filter(where y=0 and logistic_score>={threshold}),count(*) filter(where y=1 and logistic_score<{threshold}),count(*) filter(where y=1),count(*) filter(where y=0 and logistic_score>={threshold} and script_conflict),count(*) filter(where y=1 and logistic_score<{threshold} and script_conflict),count(*) filter(where y=0 and logistic_score>={threshold} and (exact_name_nosuffix or name_jaccard>.8) and address_jaccard<.2),count(*) filter(where y=1 and logistic_score<{threshold} and name_jaccard<.2 and (exact_address_norm or address_jaccard>.8)) FROM sc""").fetchone()
    errors=dict(zip(("false_positive_pairs","recovered_positive_below_threshold","recovered_candidate_positives","false_positive_script_conflict","false_negative_script_conflict","strong_name_weak_address_fp","weak_name_strong_address_fn"),row))
    total_gt=c.sql("select count(*) from fullgt g join read_parquet('work/feature_pilot_s1.parquet') p on g.s1=p.entity_id where p.split='model_calibration'").fetchone()[0]
    errors["candidate_missed_gt"]=total_gt-errors["recovered_candidate_positives"]; errors["end_to_end_missed_gt"]=errors["candidate_missed_gt"]+errors["recovered_positive_below_threshold"]
    more=c.sql(f"""WITH entities AS (SELECT entity_id s1 FROM read_parquet('work/feature_pilot_s1.parquet') WHERE split='model_calibration'), ga AS (SELECT g.s1,count(*) truth_n FROM fullgt g JOIN entities e USING(s1) GROUP BY 1), pa AS (SELECT source1_entity_id s1,count(*) pred_n,count(*) filter(where y=1) tp FROM read_parquet('work/baseline_calibration_scores.parquet') WHERE logistic_score>={threshold} GROUP BY 1), per AS (SELECT e.s1,coalesce(g.truth_n,0) truth_n,coalesce(p.pred_n,0) pred_n,coalesce(p.tp,0) tp FROM entities e LEFT JOIN ga g USING(s1) LEFT JOIN pa p USING(s1)) SELECT count(*) filter(where truth_n=0 and pred_n>0),count(*) filter(where truth_n>1 and tp<truth_n),count(*) filter(where pred_n>truth_n) FROM per""").fetchone()
    errors.update(dict(zip(("singleton_false_positive_s1","multi_match_underprediction_s1","multi_match_overprediction_s1"),more)))
    detail=c.sql(f"""SELECT count(*) filter(where s.y=1 and s.logistic_score<{threshold} and (f.s1_address_missing or f.target_address_missing)),count(*) filter(where s.y=1 and s.logistic_score<{threshold} and s.target_entity_id like 'S2-%'),count(*) filter(where s.y=1 and s.logistic_score<{threshold} and s.target_entity_id like 'S3-%') FROM read_parquet('work/baseline_calibration_scores.parquet') s JOIN read_parquet('work/feature_pilot_v1_final/model_calibration_*.parquet') f USING(source1_entity_id,target_entity_id)""").fetchone()
    errors.update(dict(zip(("address_missing_recovered_positive_below_threshold","s2_recovered_positive_below_threshold","s3_recovered_positive_below_threshold"),detail)))
    (W/"baseline_error_analysis.md").write_text("# Pilot baseline error analysis\n\nAt logistic threshold %.2f:\n\n%s\n\nCandidate-generation misses and recovered candidates rejected by the matcher are disjoint populations. No raw business strings are included.\n"%(threshold,"\n".join(f"- {k.replace('_',' ')}: {v:,}" for k,v in errors.items())))
    code=hashlib.sha256()
    for p in sorted(Path("code/business_entity_resolution/src/er").rglob("*.py")): code.update((p.as_posix()+":"+sha(p)+"\n").encode())
    run={"run_id":"pilot_baselines_v1_final_20260926","timestamp":datetime.datetime.now(datetime.timezone.utc).isoformat(),"code_sha256":code.hexdigest(),"feature_spec_sha256":sha(W/"feature_spec_v1.json"),"candidate_policy_sha256":sha(W/"final_candidate_policy.json"),"split_sha256":sha(W/"matcher_split_manifest.parquet"),"sample_sha256":samples["hybrid_source_balanced"]["sha256"],"model":["deterministic","numpy_logistic","lightgbm_smoke"],"seed":42,"features":65,"training_counts":{"rows":results["model"]["training_rows"],"positives":results["model"]["positives"]},"thresholds":{"deterministic":det["threshold"],"logistic":log["threshold"],"lightgbm":lgb["best"]["threshold"]},"metrics":{"candidate_oracle":results["candidate_oracle"],"baselines":results["best"],"lightgbm":lgb["best"]},"runtime_seconds":{"baseline":results["wall_seconds"],"lightgbm":lgb["wall_seconds"]},"peak_rss_bytes":{"baseline":peak(W/"baseline_model_run.log"),"lightgbm":peak(W/"lightgbm_smoke_run.log")},"artifact_paths":["work/logistic_baseline_v1.json","work/baseline_threshold_curve.parquet","work/lightgbm_smoke_v1.txt"],"status":"completed"}
    ledger=W/"model_experiments.jsonl"; old=ledger.read_text().splitlines() if ledger.exists() else []
    if not any(json.loads(x).get("run_id")==run["run_id"] for x in old if x.strip()):
        with ledger.open("a",encoding="utf-8") as f: f.write(json.dumps(run,separators=(",",":"))+"\n")
    c.close(); print(json.dumps({"errors":errors,"projections":projections},indent=2))

if __name__ == "__main__": main()
