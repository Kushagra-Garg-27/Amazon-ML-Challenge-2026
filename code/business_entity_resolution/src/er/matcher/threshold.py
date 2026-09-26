"""One-time threshold and set-policy selection after the model freeze."""
from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import pyarrow as pa
import pyarrow.parquet as pq

from .controlled import (MODEL_FEATURE_NAMES_V1_1, build_labels, entity_ids_for_role,
    evaluate, load_population, score_parts, sha256, source_threshold_gap,
    threshold_search, validate_model_feature_order)


def run() -> dict:
    work=Path("work")
    freeze=json.loads((work/"pre_threshold_freeze.json").read_text())
    model=lgb.Booster(model_file=(work/"final_matcher_model.txt").as_posix())
    validate_model_feature_order(model.feature_name(),freeze["features"])

    tune_files=sorted((work/"model_development_features").glob("model_tune*.parquet"))
    tune_score=score_parts(model,tune_files,work/"scores_frozen_model_tune",
                           MODEL_FEATURE_NAMES_V1_1)
    tune_ids=entity_ids_for_role(work/"model_development_samples.parquet","model_tune")
    tune=load_population("work/scores_frozen_model_tune/*.parquet",
                         work/"model_development_labels.parquet",tune_ids)
    source_gap=source_threshold_gap(tune)
    del tune

    label_info=build_labels("work/model_threshold_candidates/*.parquet",
                            work/"model_threshold_labels.parquet",("model_threshold",))
    threshold_files=sorted((work/"model_threshold_features").glob("*.parquet"))
    scoring=score_parts(model,threshold_files,work/"model_threshold_scores",
                        MODEL_FEATURE_NAMES_V1_1)
    import duckdb
    con=duckdb.connect(); ids=[x[0] for x in con.sql(
      "SELECT entity_id FROM read_parquet('work/model_development_split_manifest.parquet') WHERE split='model_threshold' ORDER BY entity_id").fetchall()]; con.close()
    pop=load_population("work/model_threshold_scores/*.parquet",
                        work/"model_threshold_labels.parquet",ids)
    global_best,global_curve=threshold_search(pop)
    conflict_best,conflict_curve=threshold_search(pop,reject_numeric_conflict=True)
    policies={"global":{"best":global_best},"global_reject_numeric_conflict":{"best":conflict_best}}
    curve=[]
    for name,rows in (("global",global_curve),("global_reject_numeric_conflict",conflict_curve)):
        for row in rows:
            curve.append({**{k:v for k,v in row.items() if k!="prediction_count_distribution"},"policy":name})
    if source_gap["eligible"]:
        pair=(source_gap["s2"]["threshold"],source_gap["s3"]["threshold"])
        policies["source_specific_from_tune"]={"thresholds":{"s2":pair[0],"s3":pair[1]},
          "best":evaluate(pop,0.0,source_thresholds=pair)}
    # Deterministic tie order favors the simpler global policy.
    order={"global":2,"global_reject_numeric_conflict":1,"source_specific_from_tune":0}
    selected=max(policies,key=lambda k:(policies[k]["best"]["macro_f0_5"],order[k]))
    pq.write_table(pa.Table.from_pylist(curve),work/"final_matcher_threshold_curve.parquet",compression="zstd")
    out={"pre_threshold_freeze_sha256":sha256(work/"pre_threshold_freeze.json"),
      "model_sha256":sha256(work/"final_matcher_model.txt"),"label_info":label_info,
      "tune_scoring":tune_score,"threshold_scoring":scoring,"source_threshold_analysis":source_gap,
      "candidate_oracle":evaluate(type(pop)(pop.y.astype('float32'),pop.entity,pop.y,pop.s2,
        pop.address_missing,pop.script_conflict,pop.numeric_conflict,pop.truth_n,pop.recovered_n,
        pop.countries,pop.entity_ids,pop.truth_breakdown),.5),
      "policies":policies,"selected":selected,"selected_result":policies[selected]["best"]}
    (work/"model_threshold_results.json").write_text(json.dumps(out,indent=2)+"\n")
    print(json.dumps({"selected":selected,"result":out["selected_result"],"source_analysis":source_gap},indent=2))
    return out


if __name__=="__main__": run()
