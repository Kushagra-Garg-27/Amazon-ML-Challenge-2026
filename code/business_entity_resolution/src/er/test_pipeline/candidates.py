"""Frozen candidate policy on test keys, with country-open-set work partitions."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import duckdb
import pyarrow.parquet as pq

from er.candidates.pilot import materialize_group
from er.candidates.policies import load_policy
from .common import record_part, sha256, valid_part

POLICY_SHA = "46fd324b3d4db7dbdf5fa4b8ce528f681335ce16f759fb93a39ba5b98a040fea"


def db(temp: Path):
    temp.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.execute("SET memory_limit='700MB'; SET threads=1; SET preserve_insertion_order=false")
    c.execute(f"SET temp_directory='{temp.as_posix()}'")
    return c


def build_target_df(root: Path = Path("work/test_rank")) -> None:
    root.mkdir(parents=True, exist_ok=True)
    c = db(root / "tmp")
    for field, col in (("name", "name_nosuffix"), ("addr", "addr_norm")):
        dest = root / f"df_{field}.parquet"
        manifest = dest.with_suffix(".json")
        inputs = {f"test_s{s}_sha256": sha256(Path(f"work/keys/test_s{s}.parquet")) for s in (2, 3)}
        inputs["field"] = field
        if valid_part(dest, manifest, inputs):
            continue
        partial = dest.with_suffix(".partial.parquet")
        partial.unlink(missing_ok=True)
        both = " UNION ALL ".join(
            f"SELECT country_norm cc,{col} val FROM read_parquet('work/keys/test_s{s}.parquet')" for s in (2, 3))
        started = time.time()
        c.execute(f"""COPY (SELECT cc,tok,count(*)::INT df FROM
          (SELECT cc,unnest(list_distinct(str_split(val,' '))) tok FROM ({both}))
          WHERE length(tok)>0 GROUP BY 1,2 ORDER BY 1,2)
          TO '{partial.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        n = pq.read_metadata(partial).num_rows
        os.replace(partial, dest)
        record_part(dest, manifest, n, inputs, wall_seconds=time.time()-started)
        print(f"DF {field}: {n} keys", flush=True)
    c.close()


def build_selection(path: Path = Path("work/test_rank/selection.parquet")) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = path.with_suffix(".json")
    inputs = {"s1_sha256": sha256(Path("work/keys/test_s1.parquet")), "physical_parts": 32}
    if valid_part(path, manifest, inputs):
        return
    c = db(path.parent / "tmp")
    partial = path.with_suffix(".partial.parquet")
    partial.unlink(missing_ok=True)
    c.execute(f"""COPY (SELECT entity_id,
      'p'||lpad(cast(hash(entity_id)%32 as varchar),2,'0') split,
      country_norm FROM read_parquet('work/keys/test_s1.parquet')
      ORDER BY split,country_norm,entity_id)
      TO '{partial.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
    n = pq.read_metadata(partial).num_rows
    os.replace(partial, path)
    record_part(path, manifest, n, inputs)
    c.close()


def run(selection: Path = Path("work/test_rank/selection.parquet"),
        out_dir: Path = Path("work/test_candidates"),
        rank_dir: Path = Path("work/test_rank/parts"),
        only: str | None = None) -> dict:
    if sha256(Path("work/final_candidate_policy.json")) != POLICY_SHA:
        raise RuntimeError("Frozen candidate policy checksum changed")
    policy = load_policy("work/final_candidate_policy.json")
    if (policy.s2_quota, policy.s3_quota, policy.heavy_sorted_cap,
        policy.partitions) != (50, 50, 100, 16):
        raise RuntimeError("Frozen candidate policy parameters changed")
    out_dir.mkdir(parents=True, exist_ok=True)
    c = db(out_dir / "preflight_tmp")
    groups = c.execute(f"SELECT split,country_norm,count(*) FROM read_parquet('{selection.as_posix()}') GROUP BY 1,2 ORDER BY 1,2").fetchall()
    c.close()
    expected = {"policy_sha256": POLICY_SHA, "selection_sha256": sha256(selection),
                "df_name_sha256": sha256(Path("work/test_rank/df_name.parquet")),
                "df_addr_sha256": sha256(Path("work/test_rank/df_addr.parquet"))}
    results = []
    for split, country, n_s1 in groups:
        name = f"{split}_{country}"
        if only and name != only:
            continue
        dest = out_dir / f"{name}.parquet"
        manifest = dest.with_suffix(".json")
        inputs = {**expected, "split": split, "country": country, "s1": n_s1}
        if valid_part(dest, manifest, inputs):
            results.append(json.loads(manifest.read_text(encoding="utf-8")))
            print(f"resumed {name}", flush=True)
            continue
        print(f"candidates {name}: {n_s1} S1", flush=True)
        metrics = materialize_group(selection, split, country, dest,
                                    keys_prefix="test", df_root=Path("work/test_rank"),
                                    rank_output=rank_dir)
        if pq.read_schema(dest).names != ["source1_entity_id", "target_entity_id", "provenance",
                                              "name_token_rank", "address_token_rank",
                                              "source_balanced_rank", "heavy_sorted_block",
                                              "name_shared_idf", "address_shared_idf"]:
            raise RuntimeError(f"Candidate schema mismatch: {dest}")
        part = record_part(dest, manifest, metrics["candidates"], inputs,
                           s1=n_s1, wall_seconds=metrics["wall_seconds"])
        results.append(part)
    return {"groups": len(results), "s1": sum(x["s1"] for x in results),
            "candidates": sum(x["rows"] for x in results)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "run"])
    parser.add_argument("--selection", type=Path, default=Path("work/test_rank/selection.parquet"))
    parser.add_argument("--out-dir", type=Path, default=Path("work/test_candidates"))
    parser.add_argument("--only")
    args = parser.parse_args()
    if args.stage == "prepare":
        build_target_df()
        build_selection(args.selection)
    else:
        print(json.dumps(run(args.selection, args.out_dir, only=args.only), indent=2), flush=True)


if __name__ == "__main__":
    main()
