"""Score every physical feature row with the immutable LightGBM model."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import lightgbm as lgb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from er.matcher.controlled import validate_model_feature_order
from .common import ResourceMonitor, record_part, sha256, valid_part

MODEL_SHA = "76ff78a7cc97e40174b6d631a6eaa062c786f0480c6693fd6387e90be6761d21"
POLICY_SHA = "ced4ee4133110f5f55c184c3d4020b5087ecac5398bdf9ebfd03f76b3662aba3"
SCHEMA = pa.schema([pa.field("source1_entity_id", pa.string(), False),
                    pa.field("target_entity_id", pa.string(), False),
                    pa.field("score", pa.float32(), False),
                    pa.field("predicted", pa.bool_(), False),
                    pa.field("target_is_s2", pa.bool_(), False),
                    pa.field("address_missing", pa.bool_(), False),
                    pa.field("script_conflict", pa.bool_(), False)])


def run(features: Path = Path("work/test_features"),
        output: Path = Path("work/test_scores"), only: str | None = None) -> dict:
    model_path = Path("work/final_matcher_model.txt")
    policy_path = Path("work/final_matcher_policy.json")
    if sha256(model_path) != MODEL_SHA or sha256(policy_path) != POLICY_SHA:
        raise RuntimeError("Frozen model/policy checksum mismatch")
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    order = policy["feature_order"]
    threshold = policy["threshold"]
    batch_size = policy["inference"]["batch_candidates"]
    if len(order) != 61 or threshold != 0.61 or batch_size != 200000 or lgb.__version__ != "4.7.0":
        raise RuntimeError("Frozen inference policy mismatch")
    model = lgb.Booster(model_file=model_path.as_posix())
    validate_model_feature_order(model.feature_name(), order)
    output.mkdir(parents=True, exist_ok=True)
    result = []
    for source in sorted(p for p in features.glob("*.parquet") if not p.name.endswith(".partial.parquet")):
        if only and source.stem != only:
            continue
        dest = output / source.name
        receipt = dest.with_suffix(".json")
        inputs = {"features_sha256": sha256(source), "model_sha256": MODEL_SHA,
                  "policy_sha256": POLICY_SHA}
        if valid_part(dest, receipt, inputs):
            result.append(json.loads(receipt.read_text(encoding="utf-8")))
            continue
        print(f"scoring {source.stem}", flush=True)
        started = time.time()
        partial = dest.with_suffix(".partial.parquet")
        partial.unlink(missing_ok=True)
        writer = pq.ParquetWriter(partial, SCHEMA, compression="zstd")
        rows = accepted = 0
        monitor = ResourceMonitor()
        monitor.__enter__()
        try:
            pf = pq.ParquetFile(source)
            columns = list(dict.fromkeys(["source1_entity_id", "target_entity_id", "target_is_s2",
                "s1_address_missing", "target_address_missing", "script_conflict", *order]))
            for batch in pf.iter_batches(batch_size=batch_size, columns=columns):
                matrix = np.column_stack([np.asarray(batch[name]).astype(np.float32, copy=False)
                                          for name in order])
                if matrix.shape[1] != 61 or not np.isfinite(matrix).all():
                    raise RuntimeError("Nonfinite or misordered model input")
                scores = model.predict(matrix,
                    num_iteration=model.best_iteration or model.current_iteration()).astype(np.float32)
                if not np.isfinite(scores).all():
                    raise RuntimeError("Nonfinite model score")
                decisions = scores >= threshold
                address = np.asarray(batch["s1_address_missing"]).astype(bool) | np.asarray(batch["target_address_missing"]).astype(bool)
                writer.write_table(pa.table({"source1_entity_id": batch["source1_entity_id"],
                    "target_entity_id": batch["target_entity_id"], "score": scores,
                    "predicted": decisions, "target_is_s2": batch["target_is_s2"],
                    "address_missing": address, "script_conflict": batch["script_conflict"]},
                    schema=SCHEMA))
                rows += len(batch)
                accepted += int(decisions.sum())
        finally:
            try:
                writer.close()
            finally:
                monitor.__exit__()
        if pq.read_metadata(partial).num_rows != pq.read_metadata(source).num_rows:
            raise RuntimeError(f"Feature/score count mismatch: {source}")
        os.replace(partial, dest)
        result.append(record_part(dest, receipt, rows, inputs, accepted=accepted,
                                  wall_seconds=time.time()-started,
                                  peak_process_rss_bytes=monitor.peak_rss_bytes,
                                  peak_temp_bytes=monitor.peak_temp_bytes))
    return {"parts": len(result), "rows": sum(x["rows"] for x in result),
            "accepted": sum(x["accepted"] for x in result)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only")
    args = parser.parse_args()
    print(json.dumps(run(only=args.only), indent=2), flush=True)


if __name__ == "__main__":
    main()
