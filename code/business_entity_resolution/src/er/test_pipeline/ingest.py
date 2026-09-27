"""Exact frozen normalization of test TSVs, with verified restartable outputs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import duckdb
import pyarrow.parquet as pq

from er.io import ingest_source, _COLS
from .common import record_part, sha256, valid_part


def run(data_dir: Path = Path("dataset/test"), out_dir: Path = Path("work/keys"),
        partition_dir: Path = Path("work/test_keys")) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    partition_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for source in (1, 2, 3):
        raw = data_dir / f"test_source{source}.tsv"
        dest = out_dir / f"test_s{source}.parquet"
        manifest = out_dir / f"test_s{source}.manifest.json"
        inputs = {"raw_sha256": sha256(raw), "normalizer_sha256": sha256(Path(__file__).parents[1] / "normalize.py")}
        if valid_part(dest, manifest, inputs):
            meta = json.loads(manifest.read_text(encoding="utf-8"))
        else:
            partial = dest.with_suffix(".partial.parquet")
            partial.unlink(missing_ok=True)
            started = time.time()
            n = ingest_source(str(raw), str(partial))
            if pq.read_schema(partial).names != _COLS or pq.read_metadata(partial).num_rows != n:
                raise RuntimeError(f"Normalized schema/count mismatch for {raw}")
            os.replace(partial, dest)
            meta = record_part(dest, manifest, n, inputs, wall_seconds=time.time() - started)
        print(f"normalized S{source}: {meta['rows']} rows", flush=True)
        results.append(meta)

    c = duckdb.connect()
    c.execute("SET memory_limit='700MB'; SET threads=1; SET preserve_insertion_order=false")
    temp = partition_dir / "tmp"
    temp.mkdir(exist_ok=True)
    c.execute(f"SET temp_directory='{temp.as_posix()}'")
    for source in (1, 2, 3):
        parent = out_dir / f"test_s{source}.parquet"
        parent_sha = sha256(parent)
        countries = [x[0] for x in c.execute(
            f"SELECT DISTINCT country_norm FROM read_parquet('{parent.as_posix()}') ORDER BY 1").fetchall()]
        for country in countries:
            if not country or not country.isalnum():
                raise RuntimeError(f"Unsafe country key: {country!r}")
            for shard in range(16):
                dest = partition_dir / f"s{source}_{country}_{shard:02}.parquet"
                manifest = dest.with_suffix(".json")
                inputs = {"parent_sha256": parent_sha, "country": country, "hash_mod": 16,
                          "hash_remainder": shard}
                if valid_part(dest, manifest, inputs):
                    continue
                partial = dest.with_suffix(".partial.parquet")
                partial.unlink(missing_ok=True)
                started = time.time()
                c.execute(f"""COPY (SELECT * FROM read_parquet('{parent.as_posix()}')
                    WHERE country_norm=? AND hash(entity_id)%16=? ORDER BY entity_id)
                    TO '{partial.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""", [country, shard])
                if pq.read_schema(partial).names != _COLS:
                    raise RuntimeError(f"Partition schema mismatch: {partial}")
                n = pq.read_metadata(partial).num_rows
                os.replace(partial, dest)
                record_part(dest, manifest, n, inputs, wall_seconds=time.time() - started)
    c.close()
    return {"sources": results, "partition_count": len(list(partition_dir.glob("s?_*.parquet")))}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("dataset/test"))
    parser.add_argument("--out-dir", type=Path, default=Path("work/keys"))
    parser.add_argument("--partition-dir", type=Path, default=Path("work/test_keys"))
    args = parser.parse_args()
    print(json.dumps(run(args.data_dir, args.out_dir, args.partition_dir), indent=2), flush=True)


if __name__ == "__main__":
    main()
