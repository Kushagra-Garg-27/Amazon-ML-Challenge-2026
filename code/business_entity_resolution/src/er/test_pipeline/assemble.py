"""Assemble candidate and thresholded match TSVs from scored partitions."""
from __future__ import annotations

import argparse
import heapq
import json
import os
from pathlib import Path
import time

import duckdb
import pyarrow.parquet as pq

from .common import record_part, sha256, valid_part


def db(temp: Path):
    temp.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.execute("SET memory_limit='700MB'; SET threads=1; SET preserve_insertion_order=false")
    c.execute(f"SET temp_directory='{temp.as_posix()}'")
    return c


def fragment(score: Path, selection: Path, root: Path) -> dict:
    name = score.stem
    split, country = name.split("_", 1)
    root.mkdir(parents=True, exist_ok=True)
    lists = root / f"{name}.parquet"
    candidate = root / f"{name}.candidate.tsv"
    matching = root / f"{name}.matching.tsv"
    manifest = root / f"{name}.json"
    inputs = {"score_sha256": sha256(score), "selection_sha256": sha256(selection)}
    if valid_part(candidate, manifest, inputs) and matching.exists():
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        if saved.get("matching_sha256") == sha256(matching):
            return saved
    c = db(root / "tmp")
    partial = lists.with_suffix(".partial.parquet")
    partial.unlink(missing_ok=True)
    started = time.time()
    c.execute(f"""COPY (SELECT s.entity_id source1_entity_id,
       coalesce(a.candidates,'') candidate_entity_ids,
       coalesce(a.matches,'') matched_entity_ids
       FROM read_parquet('{selection.as_posix()}') s
       LEFT JOIN (SELECT source1_entity_id,
         string_agg(target_entity_id,',' ORDER BY target_entity_id) candidates,
         string_agg(CASE WHEN predicted THEN target_entity_id ELSE NULL END,',' ORDER BY target_entity_id) matches
         FROM read_parquet('{score.as_posix()}') GROUP BY 1) a
       ON s.entity_id=a.source1_entity_id
       WHERE s.split=? AND s.country_norm=? ORDER BY s.entity_id)
       TO '{partial.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""", [split, country])
    os.replace(partial, lists)
    c.close()
    cand_tmp = candidate.with_suffix(".partial.tsv")
    match_tmp = matching.with_suffix(".partial.tsv")
    rows = 0
    with cand_tmp.open("w", encoding="utf-8", newline="") as cf, match_tmp.open("w", encoding="utf-8", newline="") as mf:
        for batch in pq.ParquetFile(lists).iter_batches(batch_size=50000):
            d = batch.to_pydict()
            for s1, ids, matched in zip(d["source1_entity_id"], d["candidate_entity_ids"],
                                        d["matched_entity_ids"], strict=True):
                if "\t" in s1 or "\n" in s1 or " " in ids or " " in matched:
                    raise RuntimeError("Malformed output ID/list")
                cf.write(f"{s1}\t{ids}\n")
                mf.write(f"{s1}\t{matched}\n")
                rows += 1
    os.replace(cand_tmp, candidate)
    os.replace(match_tmp, matching)
    record = record_part(candidate, manifest, rows, inputs,
                         matching_sha256=sha256(matching),
                         matching_bytes=matching.stat().st_size,
                         wall_seconds=time.time()-started)
    return record


def merge(root: Path, output: Path, kind: str) -> dict:
    files = sorted(root.glob(f"*.{kind}.tsv"))
    if not files:
        raise RuntimeError(f"No {kind} fragments")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(".partial.tsv")
    handles = [x.open("r", encoding="utf-8", newline="") for x in files]
    try:
        heap = []
        for i, handle in enumerate(handles):
            line = handle.readline()
            if line:
                heapq.heappush(heap, (line.partition("\t")[0], i, line))
        last = None
        rows = 0
        header = "source1_entity_id\t" + ("candidate_entity_ids" if kind == "candidate" else "matched_entity_ids") + "\n"
        with partial.open("w", encoding="utf-8", newline="") as out:
            out.write(header)
            while heap:
                s1, index, line = heapq.heappop(heap)
                if last is not None and s1 <= last:
                    raise RuntimeError("Duplicate or unsorted S1 in fragments")
                if line.count("\t") != 1 or not line.endswith("\n"):
                    raise RuntimeError("Malformed fragment line")
                out.write(line)
                rows += 1
                last = s1
                nxt = handles[index].readline()
                if nxt:
                    heapq.heappush(heap, (nxt.partition("\t")[0], index, nxt))
        os.replace(partial, output)
        return {"path": output.as_posix(), "rows": rows, "bytes": output.stat().st_size,
                "sha256": sha256(output)}
    finally:
        for handle in handles:
            handle.close()


def run(scores: Path = Path("work/test_scores"),
        selection: Path = Path("work/test_rank/selection.parquet"),
        root: Path = Path("work/test_assembly"),
        output: Path = Path("output"), only: str | None = None) -> dict:
    parts = []
    for score in sorted(p for p in scores.glob("*.parquet") if not p.name.endswith(".partial.parquet")):
        if only and score.stem != only:
            continue
        parts.append(fragment(score, selection, root))
    if only:
        return {"parts": parts}
    c = db(root / "tmp")
    expected = c.execute(f"SELECT count(*) FROM (SELECT DISTINCT split,country_norm FROM read_parquet('{selection.as_posix()}'))").fetchone()[0]
    c.close()
    if len(parts) != expected:
        raise RuntimeError(f"Expected {expected} complete score parts, got {len(parts)}")
    candidates = merge(root, output / "candidate_pairs.tsv", "candidate")
    matches = merge(root, output / "matching_results.tsv", "matching")
    if candidates["rows"] != matches["rows"]:
        raise RuntimeError("Output row mismatch")
    return {"candidate": candidates, "matching": matches}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only")
    args = parser.parse_args()
    print(json.dumps(run(only=args.only), indent=2), flush=True)


if __name__ == "__main__":
    main()
