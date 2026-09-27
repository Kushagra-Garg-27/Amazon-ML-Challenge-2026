"""Physical feature-v1 materialization on final test candidate partitions."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pyarrow.parquet as pq

from er.features.materialize import ARROW_SCHEMA, materialize_part
from .common import ResourceMonitor, record_part, sha256, valid_part


def run(candidates: Path = Path("work/test_candidates"),
        output: Path = Path("work/test_features"), only: str | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(os.environ.get("ER_TEST_FEATURE_TMP_DIR", str(output / "tmp")))
    result = []
    for source in sorted(p for p in candidates.glob("*.parquet") if not p.name.endswith(".partial.parquet")):
        if only and source.stem != only:
            continue
        dest = output / source.name
        receipt = dest.with_suffix(".json")
        inputs = {"candidate_sha256": sha256(source),
                  "feature_spec_sha256": sha256(Path("work/feature_spec_v1_1.json"))}
        if valid_part(dest, receipt, inputs):
            result.append(json.loads(receipt.read_text(encoding="utf-8")))
            continue
        print(f"features {source.stem}", flush=True)
        with ResourceMonitor(temp_dir) as monitor:
            metrics = materialize_part(source, dest, keys_prefix="test",
                                       temp_dir=temp_dir)
        schema = pq.read_schema(dest)
        if schema.names != ARROW_SCHEMA.names or schema.metadata != ARROW_SCHEMA.metadata:
            raise RuntimeError(f"Feature schema/version mismatch: {dest}")
        rows = pq.read_metadata(dest).num_rows
        if rows != pq.read_metadata(source).num_rows:
            raise RuntimeError(f"Candidate/feature row mismatch: {source}")
        result.append(record_part(dest, receipt, rows, inputs,
                                  wall_seconds=metrics["wall_seconds"],
                                  group_seconds=metrics["group_seconds"],
                                  peak_temp_bytes=max(metrics["peak_temp_bytes"], monitor.peak_temp_bytes),
                                  peak_process_rss_bytes=monitor.peak_rss_bytes))
    return {"parts": len(result), "rows": sum(x["rows"] for x in result)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only")
    args = parser.parse_args()
    print(json.dumps(run(only=args.only), indent=2), flush=True)


if __name__ == "__main__":
    main()
