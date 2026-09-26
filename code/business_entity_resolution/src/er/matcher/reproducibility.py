"""Representative feature and frozen-model restart reproducibility proof."""
from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pyarrow.parquet as pq

from er.features.materialize import materialize_part
from .controlled import MODEL_FEATURE_NAMES_V1_1, sha256, validate_model_feature_order


def run() -> dict:
    work=Path("work"); source=work/"model_development_candidates/model_fit_base_india.parquet"
    original=work/"model_development_features/model_fit_base_india.parquet"
    out_dir=work/"matcher_reproducibility"; out_dir.mkdir(exist_ok=True)
    regenerated=out_dir/"model_fit_base_india.parquet"; regenerated.unlink(missing_ok=True)
    materialize=materialize_part(source,regenerated)
    feature_equal=sha256(original)==sha256(regenerated)
    pf=pq.ParquetFile(regenerated); ids=[]; targets=[]; one=[]; two=[]
    model_path=work/"final_matcher_model.txt"
    model1=lgb.Booster(model_file=model_path.as_posix()); model2=lgb.Booster(model_file=model_path.as_posix())
    validate_model_feature_order(model1.feature_name(),MODEL_FEATURE_NAMES_V1_1)
    for batch in pf.iter_batches(batch_size=100_000,columns=["source1_entity_id","target_entity_id",*MODEL_FEATURE_NAMES_V1_1]):
        x=np.column_stack([np.asarray(batch[name]).astype(np.float32,copy=False) for name in MODEL_FEATURE_NAMES_V1_1])
        one.append(model1.predict(x).astype(np.float64)); two.append(model2.predict(x).astype(np.float64))
        ids.extend(batch["source1_entity_id"].to_pylist()); targets.extend(batch["target_entity_id"].to_pylist())
    s1=np.concatenate(one); s2=np.concatenate(two); threshold=.61
    d1=s1>=threshold; d2=s2>=threshold
    candidates=set(zip(ids,targets)); predictions={(s,t) for s,t,keep in zip(ids,targets,d1) if keep}
    result={"partition":"model_fit_base_india","rows":len(ids),
      "original_feature_sha256":sha256(original),"regenerated_feature_sha256":sha256(regenerated),
      "feature_checksum_equal":feature_equal,"model_sha256":sha256(model_path),
      "max_absolute_score_difference":float(np.max(np.abs(s1-s2))),
      "identical_threshold_decisions":bool(np.array_equal(d1,d2)),
      "identical_prediction_sets":bool(np.array_equal(d1,d2)),
      "predictions_subset_of_candidates":predictions<=candidates,
      "predicted_pairs":len(predictions),"materialization":materialize}
    (work/"matcher_reproducibility.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result,indent=2)); return result


if __name__=="__main__": run()
