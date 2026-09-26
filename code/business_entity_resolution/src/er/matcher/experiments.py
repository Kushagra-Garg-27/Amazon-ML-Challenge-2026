"""Predeclared bounded experiments for the controlled matcher phase."""
from __future__ import annotations

import gc
import json
import time
from pathlib import Path

import duckdb
import numpy as np

from er.features.schema import FEATURE_NAMES
from .controlled import (DEFAULT_PARAMS, FEATURE_GROUPS, HARD_SCORE, MODEL_FEATURE_NAMES_V1_1,
    connect, entity_ids_for_role, evaluate, load_population, save_curve, score_parts,
    sha256, threshold_search, train_model)

CONFIGS = {
  "leaves15_depth6": ({**DEFAULT_PARAMS,"num_leaves":15,"max_depth":6,"min_data_in_leaf":100},180),
  "leaves31_depth8": ({**DEFAULT_PARAMS,"num_leaves":31,"max_depth":8,"min_data_in_leaf":100},180),
  "leaves31_slow": ({**DEFAULT_PARAMS,"num_leaves":31,"max_depth":8,"min_data_in_leaf":200,"learning_rate":.03},260),
  "leaves15_fast": ({**DEFAULT_PARAMS,"num_leaves":15,"max_depth":7,"min_data_in_leaf":200,"learning_rate":.08,"feature_fraction":.8},120),
}


def files(prefix: str) -> list[Path]:
    return sorted(Path("work/model_development_features").glob(prefix+"*.parquet"))


def score_eval(model, source_files, score_dir: Path, features, ids, labels: Path):
    timing=score_parts(model,source_files,score_dir,features)
    pop=load_population((score_dir/"*.parquet").as_posix(),labels,ids)
    best,curve=threshold_search(pop)
    oracle_pop=type(pop)(pop.y.astype(np.float32),pop.entity,pop.y,pop.s2,pop.address_missing,
                         pop.script_conflict,pop.numeric_conflict,pop.truth_n,pop.recovered_n,pop.countries,
                         pop.entity_ids,pop.truth_breakdown)
    oracle=evaluate(oracle_pop,.5)
    return timing,best,curve,oracle,pop


def build_mined_samples(fit_score_glob: str, labels: Path, selection: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True,exist_ok=True); con=connect()
    con.execute(f"""CREATE TEMP VIEW base AS SELECT f.*,l.y,s.score,
      sel.tier_a,sel.tier_b,sel.tier_c
      FROM read_parquet('work/model_development_features/model_fit*.parquet') f
      JOIN read_parquet('{labels.as_posix()}') l USING(source1_entity_id,target_entity_id)
      JOIN read_parquet('{fit_score_glob}') s USING(source1_entity_id,target_entity_id)
      JOIN read_parquet('{selection.as_posix()}') sel ON f.source1_entity_id=sel.entity_id
      WHERE sel.sample_role LIKE 'model_fit%'""")
    out={}
    for tier,pred in (("a","tier_a"),("b","tier_b"),("c","tier_c")):
        path=out_dir/f"mined_tier_{tier}.parquet"; path.unlink(missing_ok=True)
        con.execute(f"""COPY (WITH r AS (SELECT *,
          row_number() OVER(PARTITION BY source1_entity_id,y,target_is_s2 ORDER BY score DESC,target_entity_id) mined_rn,
          row_number() OVER(PARTITION BY source1_entity_id,y,target_is_s2 ORDER BY md5(target_entity_id||':mined-random-v1'),target_entity_id) random_rn
          FROM base WHERE {pred})
          SELECT * EXCLUDE(score,tier_a,tier_b,tier_c,mined_rn,random_rn) FROM r
          WHERE y=1 OR mined_rn<=10 OR random_rn<=2 ORDER BY source1_entity_id,target_entity_id)
          TO '{path.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        row=con.sql(f"SELECT count(*),sum(y),count(*)-sum(y),count(distinct source1_entity_id),count(*) filter(where y=0 and target_is_s2),count(*) filter(where y=0 and target_is_s3) FROM read_parquet('{path.as_posix()}')").fetchone()
        out[f"mined_tier_{tier}"]=dict(zip(("rows","positives","negatives","s1","negative_s2","negative_s3"),row))|{"path":path.as_posix(),"bytes":path.stat().st_size,"sha256":sha256(path)}
    con.close(); return out


def build_tune_validation(labels: Path, output: Path) -> dict:
    con=connect(); output.unlink(missing_ok=True)
    con.execute(f"""COPY (WITH base AS (SELECT f.*,l.y FROM read_parquet('work/model_development_features/model_tune*.parquet') f JOIN read_parquet('{labels.as_posix()}') l USING(source1_entity_id,target_entity_id)),r AS (
      SELECT *,row_number() OVER(PARTITION BY source1_entity_id,y,target_is_s2 ORDER BY {HARD_SCORE} DESC,target_entity_id) rn FROM base)
      SELECT * EXCLUDE(rn) FROM r WHERE y=1 OR rn<=5 ORDER BY source1_entity_id,target_entity_id)
      TO '{output.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
    row=con.sql(f"SELECT count(*),sum(y),count(distinct source1_entity_id) FROM read_parquet('{output.as_posix()}')").fetchone(); con.close()
    return {"rows":row[0],"positives":row[1],"s1":row[2],"bytes":output.stat().st_size,"sha256":sha256(output)}


def run() -> dict:
    started=time.time(); work=Path("work"); labels=work/"model_development_labels.parquet"
    sample_dir=work/"model_development_negative_samples"
    samples=json.loads((work/"model_development_negative_samples.json").read_text())
    baseline_ids=entity_ids_for_role(work/"model_development_samples.parquet","baseline_dev%")
    baseline_files=files("baseline_dev")
    results={"predeclared_configs":CONFIGS,"selection_rules":{
      "sampling":"current_hybrid unless another policy improves baseline-dev macro F0.5 by at least 0.001; ties use fewer negatives",
      "features":"all_v1 unless an ablation improves macro F0.5 by at least 0.002 without reducing either country macro by >0.01",
      "training_tier":"smallest tier within 0.001 macro F0.5 of the best nested tier",
      "configuration":"highest model-tune macro F0.5, then fewer leaves, then fewer rounds"}}

    fixed={**DEFAULT_PARAMS,"num_leaves":15,"max_depth":7,"min_data_in_leaf":150,"learning_rate":.06}
    neg={}
    initial_mixed=None
    for policy in ("current_hybrid","mixed"):
        sample=sample_dir/f"{policy}_tier_b.parquet"
        model,train=train_model(sample,MODEL_FEATURE_NAMES_V1_1,fixed,120)
        timing,best,curve,oracle,pop=score_eval(model,baseline_files,work/f"scores_negative_{policy}",MODEL_FEATURE_NAMES_V1_1,baseline_ids,labels)
        neg[policy]={"sample":samples[f"{policy}_tier_b"],"train":train,"inference":timing,"best":best,"candidate_oracle":oracle}
        save_curve(curve,work/f"negative_{policy}_curve.parquet",{"experiment":policy})
        if policy=="mixed": initial_mixed=model
        else: del model
        del pop; gc.collect()

    fit_scoring=score_parts(initial_mixed,files("model_fit"),work/"scores_mining_fit",MODEL_FEATURE_NAMES_V1_1)
    mined=build_mined_samples("work/scores_mining_fit/*.parquet",labels,work/"model_development_samples.parquet",sample_dir)
    samples.update(mined); (work/"model_development_negative_samples.json").write_text(json.dumps(samples,indent=2)+"\n")
    mined_model,mined_train=train_model(sample_dir/"mined_tier_b.parquet",MODEL_FEATURE_NAMES_V1_1,fixed,120)
    timing,best,curve,oracle,pop=score_eval(mined_model,baseline_files,work/"scores_negative_mined",MODEL_FEATURE_NAMES_V1_1,baseline_ids,labels)
    neg["mined"]={"sample":mined["mined_tier_b"],"train":mined_train,"inference":timing,"best":best,"candidate_oracle":oracle,"mining_inference":fit_scoring}
    save_curve(curve,work/"negative_mined_curve.parquet",{"experiment":"mined"}); del pop,mined_model,initial_mixed; gc.collect()
    base=neg["current_hybrid"]["best"]["macro_f0_5"]
    eligible=[k for k,v in neg.items() if k=="current_hybrid" or v["best"]["macro_f0_5"]>=base+.001]
    selected_sampling=max(eligible,key=lambda k:(neg[k]["best"]["macro_f0_5"],-neg[k]["sample"]["negatives"],k))
    results["negative_sampling"]={"experiments":neg,"selected":selected_sampling,"mined_samples":mined}

    ablations={}; aliases={"without_rapidfuzz":"all_non_fuzzy"}
    train_path=sample_dir/f"{selected_sampling}_tier_b.parquet"
    for name,features in FEATURE_GROUPS.items():
        if name in aliases:
            continue
        model,train=train_model(train_path,features,fixed,120)
        timing,best,curve,oracle,pop=score_eval(model,baseline_files,work/f"scores_ablation_{name}",features,baseline_ids,labels)
        ablations[name]={"feature_count":len(features),"features":list(features),"train":train,"inference":timing,"best":best}
        save_curve(curve,work/f"ablation_{name}_curve.parquet",{"experiment":name})
        del model,pop; gc.collect()
    for alias,source in aliases.items(): ablations[alias]={**ablations[source],"alias_of":source}
    all_score=ablations["all_v1"]["best"]["macro_f0_5"]
    eligible=["all_v1"]
    for name,v in ablations.items():
        if name=="all_v1" or "alias_of" in v: continue
        b=v["best"]; a=ablations["all_v1"]["best"]
        if b["macro_f0_5"]>=all_score+.002 and all(b.get(f"{cc}_macro_f0_5",0)>=a.get(f"{cc}_macro_f0_5",0)-.01 for cc in ("india","us")):
            eligible.append(name)
    selected_features=max(eligible,key=lambda k:(ablations[k]["best"]["macro_f0_5"],-ablations[k]["feature_count"],k))
    feature_names=FEATURE_GROUPS[selected_features]
    results["ablations"]={"experiments":ablations,"selected":selected_features,"selected_features":list(feature_names)}

    learning={}
    for tier in ("a","b","c"):
        path=sample_dir/f"{selected_sampling}_tier_{tier}.parquet"
        model,train=train_model(path,feature_names,fixed,160)
        timing,best,curve,oracle,pop=score_eval(model,baseline_files,work/f"scores_learning_{tier}",feature_names,baseline_ids,labels)
        learning[tier]={"train":train,"inference":timing,"best":best,"candidate_oracle":oracle}
        save_curve(curve,work/f"learning_{tier}_curve.parquet",{"tier":tier})
        del model,pop; gc.collect()
    best_learning=max(v["best"]["macro_f0_5"] for v in learning.values())
    selected_tier=next(t for t in ("a","b","c") if learning[t]["best"]["macro_f0_5"]>=best_learning-.001)
    results["learning_curve"]={"experiments":learning,"selected_tier":selected_tier}

    tune_validation=build_tune_validation(labels,work/"model_tune_validation_sample.parquet")
    tune_ids=entity_ids_for_role(work/"model_development_samples.parquet","model_tune")
    tune_files=files("model_tune")
    configs={}; train_path=sample_dir/f"{selected_sampling}_tier_{selected_tier}.parquet"
    for name,(params,rounds) in CONFIGS.items():
        model,train=train_model(train_path,feature_names,params,rounds,
          valid_sample=work/"model_tune_validation_sample.parquet",early_stopping=20)
        timing,best,curve,oracle,pop=score_eval(model,tune_files,work/f"scores_config_{name}",feature_names,tune_ids,labels)
        configs[name]={"params":params,"requested_rounds":rounds,"train":train,"inference":timing,"best":best,"candidate_oracle":oracle}
        save_curve(curve,work/f"config_{name}_curve.parquet",{"configuration":name})
        del model,pop; gc.collect()
    selected_config=max(configs,key=lambda k:(configs[k]["best"]["macro_f0_5"],-configs[k]["params"]["num_leaves"],-configs[k]["train"]["best_iteration"],k))
    results["configuration_comparison"]={"validation_sample":tune_validation,"experiments":configs,"selected":selected_config}

    cfg=configs[selected_config]; rounds=cfg["train"]["best_iteration"]
    final_model,final_train=train_model(train_path,feature_names,cfg["params"],rounds,work/"final_matcher_model.txt")
    results["freeze_before_threshold"]={"negative_sampling":selected_sampling,"feature_group":selected_features,
      "features":list(feature_names),"training_tier":selected_tier,"training_sample":train_path.as_posix(),
      "training_sample_sha256":sha256(train_path),"configuration":selected_config,
      "params":cfg["params"],"rounds":rounds,"model":final_train,
      "candidate_policy_sha256":sha256(work/"final_candidate_policy.json"),
      "feature_spec_sha256":sha256(work/"feature_spec_v1_1.json"),
      "development_split_sha256":sha256(work/"model_development_split_manifest.parquet"),
      "lightgbm_version":__import__('lightgbm').__version__}
    results["wall_seconds"]=time.time()-started
    (work/"model_development_results.json").write_text(json.dumps(results,indent=2)+"\n")
    print(json.dumps(results["freeze_before_threshold"],indent=2))
    return results


if __name__=="__main__": run()
