"""Controlled matcher development orchestration entry point."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from er.matcher.development import build_samples, build_split


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("split", "samples"))
    args = ap.parse_args()
    if args.stage == "split":
        out = build_split(
            Path("work/model_development_split_manifest.parquet"),
            Path("work/model_development_split_report.md"),
            Path("work/model_development_split_checksums.json"),
        )
    else:
        out = build_samples(Path("work/model_development_samples.parquet"))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
