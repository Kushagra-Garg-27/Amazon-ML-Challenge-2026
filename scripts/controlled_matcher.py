"""CLI for the bounded controlled model-development stages."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from er.features.audit_v1 import audit
from er.matcher.controlled import build_labels, build_training_samples


def main() -> None:
    ap=argparse.ArgumentParser(); ap.add_argument("stage",choices=("labels","audit","samples")); args=ap.parse_args()
    if args.stage=="labels":
        out=build_labels("work/model_development_candidates/*.parquet",
          Path("work/model_development_labels.parquet"),
          ("model_fit","baseline_dev","model_tune"))
    elif args.stage=="audit":
        out=audit("work/model_development_features/*base*.parquet",
          Path("work/model_development_labels.parquet"),
          Path("work/feature_audit_v1.json"),Path("work/feature_audit_v1.md"),
          Path("work/feature_spec_v1_1.json"),Path("work/feature_spec_v1_1.md"))
    else:
        out=build_training_samples("work/model_development_features/*.parquet",
          Path("work/model_development_labels.parquet"),
          Path("work/model_development_samples.parquet"),
          Path("work/model_development_negative_samples"))
        Path("work/model_development_negative_samples.json").write_text(json.dumps(out,indent=2)+"\n")
    print(json.dumps(out,indent=2))


if __name__=="__main__": main()
