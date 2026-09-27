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


def scored_rows(score: Path):
    """Yield identities in the sorted order preserved by feature scoring."""
    columns = ["source1_entity_id", "target_entity_id", "predicted"]
    for batch in pq.ParquetFile(score).iter_batches(batch_size=100000, columns=columns):
        data = batch.to_pydict()
        yield from zip(*(data[column] for column in columns), strict=True)


def fragment(score: Path, selection: Path, root: Path) -> dict:
    name = score.stem
    split, country = name.split("_", 1)
    root.mkdir(parents=True, exist_ok=True)
    candidate = root / f"{name}.candidate.tsv"
    matching = root / f"{name}.matching.tsv"
    manifest = root / f"{name}.json"
    inputs = {"score_sha256": sha256(score), "selection_sha256": sha256(selection)}
    if valid_part(candidate, manifest, inputs) and matching.exists():
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        if saved.get("matching_sha256") == sha256(matching):
            return saved
    started = time.time()
    c = db(root / "tmp")
    try:
        selected = [row[0] for row in c.execute(
            f"SELECT entity_id FROM read_parquet('{selection.as_posix()}') "
            "WHERE split=? AND country_norm=? ORDER BY entity_id", [split, country]).fetchall()]
    finally:
        c.close()
    cand_tmp = candidate.with_suffix(".partial.tsv")
    match_tmp = matching.with_suffix(".partial.tsv")
    rows = candidate_ids = matched_ids = 0
    stream = iter(scored_rows(score))
    current = next(stream, None)
    previous_s1 = None
    with cand_tmp.open("w", encoding="utf-8", newline="") as cf, match_tmp.open("w", encoding="utf-8", newline="") as mf:
        for s1 in selected:
            if previous_s1 is not None and s1 <= previous_s1:
                raise RuntimeError("Duplicate or unordered selection S1")
            if "\t" in s1 or "\n" in s1 or "\r" in s1:
                raise RuntimeError("Malformed S1 ID")
            if current is not None and current[0] < s1:
                raise RuntimeError(f"Scored S1 missing from selection: {current[0]}")
            candidates = []
            matches = []
            previous_target = None
            while current is not None and current[0] == s1:
                _, target, predicted = current
                if previous_target is not None and target <= previous_target:
                    raise RuntimeError(f"Duplicate or unordered scored target for {s1}")
                if not target.startswith(("S2-", "S3-")) or any(
                    char in target for char in (",", " ", "\t", "\n", "\r")):
                    raise RuntimeError(f"Malformed scored target ID for {s1}")
                candidates.append(target)
                if predicted:
                    matches.append(target)
                previous_target = target
                current = next(stream, None)
            cf.write(f"{s1}\t{','.join(candidates)}\n")
            mf.write(f"{s1}\t{','.join(matches)}\n")
            candidate_ids += len(candidates)
            matched_ids += len(matches)
            rows += 1
            previous_s1 = s1
    if current is not None:
        raise RuntimeError(f"Scored S1 missing from selection: {current[0]}")
    os.replace(cand_tmp, candidate)
    os.replace(match_tmp, matching)
    record = record_part(candidate, manifest, rows, inputs,
                         matching_sha256=sha256(matching),
                         matching_bytes=matching.stat().st_size,
                         candidate_ids=candidate_ids, matched_ids=matched_ids,
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
