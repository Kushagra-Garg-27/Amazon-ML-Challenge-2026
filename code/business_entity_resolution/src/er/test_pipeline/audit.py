"""Independent streaming audit of release TSVs against raw IDs and Parquet parts."""
from __future__ import annotations

from array import array
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

import duckdb
import pyarrow.parquet as pq

from .common import save_json, sha256


def part_membership(selection: Path) -> dict[str, str]:
    result = {}
    for batch in pq.ParquetFile(selection).iter_batches(batch_size=100000,
        columns=["entity_id", "split", "country_norm"]):
        d = batch.to_pydict()
        for s1, split, country in zip(d["entity_id"], d["split"], d["country_norm"], strict=True):
            if s1 in result:
                raise RuntimeError("Duplicate selection S1")
            result[s1] = f"{split}_{country}"
    return result


def raw_s1_ids(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        if stream.readline() != "entity_id\tbusiness_name\tbusiness_address\tcountry\n":
            raise RuntimeError("Raw S1 schema mismatch")
        ids = [line.partition("\t")[0] for line in stream]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Duplicate raw S1")
    return sorted(ids)


def read_pair(candidate: Path, matching: Path, selection: Path,
              raw: Path = Path("dataset/test/test_source1.tsv")) -> dict:
    membership = part_membership(selection)
    expected = raw_s1_ids(raw)
    if set(membership) != set(expected):
        raise RuntimeError("Selection differs from raw S1 IDs")
    cand_hash = {name: hashlib.sha256() for name in set(membership.values())}
    match_hash = {name: hashlib.sha256() for name in set(membership.values())}
    candidate_counts = array("I")
    match_counts = array("I")
    total_candidates = total_matches = empty_candidates = empty_matches = 0
    with candidate.open("r", encoding="utf-8", newline="") as cf, matching.open("r", encoding="utf-8", newline="") as mf:
        if cf.readline() != "source1_entity_id\tcandidate_entity_ids\n":
            raise RuntimeError("Candidate header mismatch")
        if mf.readline() != "source1_entity_id\tmatched_entity_ids\n":
            raise RuntimeError("Matching header mismatch")
        for n, required in enumerate(expected, start=2):
            cline = cf.readline()
            mline = mf.readline()
            if not cline or not mline or not cline.endswith("\n") or not mline.endswith("\n"):
                raise RuntimeError(f"Missing/unterminated output line {n}")
            if cline.count("\t") != 1 or mline.count("\t") != 1:
                raise RuntimeError(f"Malformed output line {n}")
            cs1, cvalues = cline[:-1].split("\t")
            ms1, mvalues = mline[:-1].split("\t")
            if cs1 != required or ms1 != required:
                raise RuntimeError(f"Wrong S1 order or identity at line {n}")
            candidates = cvalues.split(",") if cvalues else []
            matches = mvalues.split(",") if mvalues else []
            for label, values in (("candidate", candidates), ("matching", matches)):
                if values != sorted(set(values)):
                    raise RuntimeError(f"Duplicate or unordered {label} targets at line {n}")
                if any(not x.startswith(("S2-", "S3-")) or " " in x or "\r" in x for x in values):
                    raise RuntimeError(f"Invalid {label} target ID at line {n}")
            if not set(matches).issubset(candidates):
                raise RuntimeError(f"Matches outside candidates at line {n}")
            part = membership[required]
            for target in candidates:
                cand_hash[part].update(f"{required}\t{target}\n".encode("utf-8"))
            for target in matches:
                match_hash[part].update(f"{required}\t{target}\n".encode("utf-8"))
            candidate_counts.append(len(candidates))
            match_counts.append(len(matches))
            total_candidates += len(candidates)
            total_matches += len(matches)
            empty_candidates += not candidates
            empty_matches += not matches
        if cf.readline() or mf.readline():
            raise RuntimeError("Extra output rows")
    return {"rows": len(expected), "candidate_ids": total_candidates,
            "matched_ids": total_matches, "empty_candidates": empty_candidates,
            "empty_matches": empty_matches,
            "candidate_sha256": {k: v.hexdigest() for k, v in cand_hash.items()},
            "matching_sha256": {k: v.hexdigest() for k, v in match_hash.items()},
            "candidate_counts": candidate_counts, "matching_counts": match_counts}


def parquet_identity(path: Path, accepted_only: bool = False) -> tuple[str, int]:
    digest = hashlib.sha256()
    n = 0
    cols = ["source1_entity_id", "target_entity_id"] + (["predicted", "score"] if accepted_only else [])
    last = None
    for batch in pq.ParquetFile(path).iter_batches(batch_size=100000, columns=cols):
        d = batch.to_pydict()
        rows = zip(*(d[x] for x in cols), strict=True)
        for record in rows:
            s1, target = record[:2]
            if last is not None and (s1, target) <= last:
                raise RuntimeError(f"Duplicate/unsorted artifact identity in {path}")
            last = (s1, target)
            if accepted_only:
                predicted, value = record[2:]
                if value is None or not math.isfinite(value) or predicted != (value >= 0.61):
                    raise RuntimeError(f"Nonfinite or wrong decision in {path}")
                if not predicted:
                    continue
            digest.update(f"{s1}\t{target}\n".encode("utf-8"))
            n += 1
    return digest.hexdigest(), n


def target_existence(candidates: Path) -> int:
    c = duckdb.connect()
    c.execute("SET memory_limit='700MB'; SET threads=1")
    tmp = Path("work/test_audit_tmp")
    tmp.mkdir(parents=True, exist_ok=True)
    c.execute(f"SET temp_directory='{tmp.as_posix()}'")
    target = "SELECT entity_id FROM read_parquet('work/keys/test_s2.parquet') UNION ALL SELECT entity_id FROM read_parquet('work/keys/test_s3.parquet')"
    missing = c.execute(f"""SELECT count(*) FROM read_parquet('{(candidates / '*.parquet').as_posix()}') p
       ANTI JOIN ({target}) t ON p.target_entity_id=t.entity_id""").fetchone()[0]
    c.close()
    return missing


def summarize_counts(values: array) -> dict:
    ordered = sorted(values)
    n = len(ordered)
    return {"mean": sum(values)/n, "median": ordered[n//2],
            "p95": ordered[math.ceil(.95*n)-1], "p99": ordered[math.ceil(.99*n)-1],
            "max": ordered[-1]}


def run(output: Path = Path("output"), selection: Path = Path("work/test_rank/selection.parquet"),
        candidates: Path = Path("work/test_candidates"),
        scores: Path = Path("work/test_scores")) -> dict:
    candidate_tsv = output / "candidate_pairs.tsv"
    matching_tsv = output / "matching_results.tsv"
    parsed = read_pair(candidate_tsv, matching_tsv, selection)
    parts = sorted(p for p in candidates.glob("*.parquet") if not p.name.endswith(".partial.parquet"))
    expected = len(parsed["candidate_sha256"])
    if len(parts) != expected or len([p for p in scores.glob("*.parquet") if not p.name.endswith(".partial.parquet")]) != expected:
        raise RuntimeError("Missing candidate/score partition")
    for candidate in parts:
        name = candidate.stem
        score_path = scores / candidate.name
        candidate_hash, candidate_n = parquet_identity(candidate)
        score_all_hash, score_n = parquet_identity(score_path)
        accepted_hash, accepted_n = parquet_identity(score_path, accepted_only=True)
        if candidate_hash != parsed["candidate_sha256"][name] or candidate_hash != score_all_hash:
            raise RuntimeError(f"Candidate/output/score identity mismatch: {name}")
        if accepted_hash != parsed["matching_sha256"][name]:
            raise RuntimeError(f"Threshold/output mismatch: {name}")
        if candidate_n != score_n:
            raise RuntimeError(f"Candidate/score count mismatch: {name}")
    if parsed["candidate_ids"] != sum(pq.read_metadata(x).num_rows for x in parts):
        raise RuntimeError("Candidate global row count mismatch")
    unknown = target_existence(candidates)
    if unknown:
        raise RuntimeError(f"Unknown target IDs: {unknown}")
    result = {"status": "PASS", "rows": parsed["rows"],
              "candidate_ids": parsed["candidate_ids"], "matched_ids": parsed["matched_ids"],
              "empty_candidates": parsed["empty_candidates"], "empty_matches": parsed["empty_matches"],
              "candidate_distribution": summarize_counts(parsed["candidate_counts"]),
              "matching_distribution": summarize_counts(parsed["matching_counts"]),
              "unknown_targets": unknown,
              "candidate_file": {"bytes": candidate_tsv.stat().st_size, "sha256": sha256(candidate_tsv),
                                 "line_count": parsed["rows"]+1},
              "matching_file": {"bytes": matching_tsv.stat().st_size, "sha256": sha256(matching_tsv),
                                "line_count": parsed["rows"]+1}}
    save_json(Path("work/test_output_integrity.json"), result)
    Path("work/test_output_integrity.md").write_text("# Independent output integrity\n\nPASS\n\n```json\n" + json.dumps(result, indent=2) + "\n```\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("output"))
    args = parser.parse_args()
    print(json.dumps(run(output=args.output), indent=2), flush=True)
