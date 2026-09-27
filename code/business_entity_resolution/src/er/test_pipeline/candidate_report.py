"""Structural candidate policy audit and per-part metrics without labels."""
from __future__ import annotations

import json
import csv
import re
from datetime import datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .common import save_json, sha256


def samples(path: Path, column: str) -> list[tuple[float,int]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as stream:
        return [(datetime.fromisoformat(row["timestamp_utc"].replace("Z", "+00:00")).timestamp(),
                 int(row[column])) for row in csv.DictReader(stream) if row.get(column)]


def run(root: Path = Path("work/test_candidates")) -> dict:
    files = sorted(p for p in root.glob("*.parquet") if not p.name.endswith(".partial.parquet"))
    tmp = Path("work/test_candidate_audit_tmp")
    tmp.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.execute("SET memory_limit='700MB'; SET threads=1")
    c.execute(f"SET temp_directory='{tmp.as_posix()}'")
    rss_samples = samples(Path("work/test_candidate_resource_samples.csv"), "working_set_bytes")
    temp_samples = samples(Path("work/test_candidate_temp_samples.csv"), "temp_bytes")
    attempt_log = Path("work/test_candidate_run.log")
    attempts: dict[str, int] = {}
    if attempt_log.exists():
        for line in attempt_log.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^candidates (p\d+_[^:]+):", line)
            if match:
                attempts[match.group(1)] = attempts.get(match.group(1), 0) + 1
    failures: dict[str, int] = {}
    failure_log = Path("work/test_candidate_failures.jsonl")
    if failure_log.exists():
        for line in failure_log.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            part = event["part"]
            failures[part] = failures.get(part, 0) + 1
    expected = c.execute("SELECT count(*) FROM (SELECT DISTINCT split,country_norm FROM read_parquet('work/test_rank/selection.parquet'))").fetchone()[0]
    if len(files) != expected:
        raise RuntimeError(f"Expected {expected} candidate parts, got {len(files)}")
    parts = []
    for path in files:
        stem = path.stem
        receipt = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        if receipt["sha256"] != sha256(path):
            raise RuntimeError(f"Candidate checksum mismatch: {path}")
        p = path.as_posix()
        n, unique, s2, s3, bad_prefix, zero_prov, bad_rank, heavy_only_over, bad_rank_bit = c.execute(f"""
          SELECT count(*),count(DISTINCT (source1_entity_id,target_entity_id)),
            count(*) FILTER(WHERE target_entity_id LIKE 'S2-%'),
            count(*) FILTER(WHERE target_entity_id LIKE 'S3-%'),
            count(*) FILTER(WHERE target_entity_id NOT LIKE 'S2-%' AND target_entity_id NOT LIKE 'S3-%'),
            count(*) FILTER(WHERE provenance<1 OR provenance>15),
            count(*) FILTER(WHERE name_token_rank>50 OR address_token_rank>50),
            count(*) FILTER(WHERE provenance=1 AND heavy_sorted_block AND source_balanced_rank>100),
            count(*) FILTER(WHERE ((provenance & 4) != 0) != (name_token_rank>0)
              OR ((provenance & 8) != 0) != (address_token_rank>0))
          FROM read_parquet('{p}')""").fetchone()
        if n != receipt["rows"] or n != unique or n != s2+s3 or bad_prefix or zero_prov or bad_rank or heavy_only_over or bad_rank_bit:
            raise RuntimeError(f"Candidate identity/policy failure: {path}")
        selection = "work/test_rank/selection.parquet"
        invalid_s1 = c.execute(f"""SELECT count(*) FROM read_parquet('{p}') x ANTI JOIN
           read_parquet('{selection}') s ON x.source1_entity_id=s.entity_id
           AND s.split=? AND s.country_norm=?""", stem.split("_",1)).fetchone()[0]
        if invalid_s1:
            raise RuntimeError(f"Out-of-partition S1: {path}")
        mean, p95, p99, maximum, zero = c.execute(f"""SELECT avg(coalesce(n.n,0)),
           quantile_cont(coalesce(n.n,0),.95),quantile_cont(coalesce(n.n,0),.99),
           max(coalesce(n.n,0)),count(*) FILTER(WHERE n.n IS NULL)
           FROM read_parquet('{selection}') s LEFT JOIN
           (SELECT source1_entity_id,count(*) n FROM read_parquet('{p}') GROUP BY 1) n
           ON s.entity_id=n.source1_entity_id
           WHERE s.split=? AND s.country_norm=?""", stem.split("_",1)).fetchone()
        ranks = {field: Path("work/test_rank/parts") / f"{stem}_rank_{field}.parquet"
                 for field in ("name", "addr")}
        ranks["heavy"] = Path("work/test_rank/parts") / f"{stem}_heavy_rank.parquet"
        if any(not x.is_file() for x in ranks.values()):
            raise RuntimeError(f"Missing rank artifact for {stem}")
        heavy_file = ranks["heavy"]
        heavy_cap_violations = c.execute(f"""SELECT count(*) FROM read_parquet('{p}') p
           JOIN read_parquet('{heavy_file.as_posix()}') h
           ON p.source1_entity_id=h.s1 AND p.target_entity_id=h.mid
           WHERE p.provenance=1 AND h.rk>100""").fetchone()[0]
        if heavy_cap_violations:
            raise RuntimeError(f"Heavy sorted-name cap violation: {path}")
        provenance = dict(c.execute(f"SELECT provenance,count(*) FROM read_parquet('{p}') GROUP BY 1 ORDER BY 1").fetchall())
        ended = path.with_suffix(".json").stat().st_mtime
        started = ended - receipt.get("wall_seconds", 0)
        rss_part = [v for t,v in rss_samples if started <= t <= ended]
        temp_part = [v for t,v in temp_samples if started <= t <= ended]
        row = {"part": stem, "s1": receipt["s1"], "candidates": n, "s2": s2, "s3": s3,
               "provenance": {str(k): v for k,v in provenance.items()},
               "candidate_mean": mean, "candidate_p95": p95,
               "candidate_p99": p99, "candidate_max": maximum,
               "zero_candidate_s1": zero, "heavy_cap_violations": heavy_cap_violations,
               "rank_artifacts": {field: {"bytes": file.stat().st_size, "sha256": sha256(file)}
                                  for field,file in ranks.items()},
               "sha256": receipt["sha256"], "bytes": receipt["bytes"],
               "wall_seconds": receipt.get("wall_seconds"),
               "attempts_recorded": attempts.get(stem, 1),
               "retries_recorded": max(0, attempts.get(stem, 1) - 1),
               "failures_recorded": failures.get(stem, 0),
               "interruptions_recorded": max(0, attempts.get(stem, 1) - 1 - failures.get(stem, 0)),
               "peak_process_rss_sampled_bytes": max(rss_part) if rss_part else None,
               "peak_temp_sampled_bytes": max(temp_part) if temp_part else None,
               "resource_sample_coverage": bool(rss_part)}
        parts.append(row)
        print(f"audited {stem}: {n:,} candidates", flush=True)
    normalized_s1 = c.execute("SELECT count(*) FROM read_parquet('work/keys/test_s1.parquet')").fetchone()[0]
    c.close()
    total = sum(x["candidates"] for x in parts)
    result = {"status": "PASS", "parts": len(parts), "s1": sum(x["s1"] for x in parts),
              "candidates": total, "s2": sum(x["s2"] for x in parts),
              "s3": sum(x["s3"] for x in parts), "partitions": parts}
    peak = samples(Path("work/test_candidate_resource_samples.csv"), "peak_working_set_bytes")
    free_ram = samples(Path("work/test_candidate_resource_samples.csv"), "free_ram_bytes")
    free_disk = samples(Path("work/test_candidate_resource_samples.csv"), "free_disk_bytes")
    result["resources"] = {"peak_process_rss_bytes": max((v for _,v in peak), default=None),
                           "peak_temp_sampled_bytes": max((v for _,v in temp_samples), default=None),
                           "minimum_free_ram_bytes": min((v for _,v in free_ram), default=None),
                           "minimum_free_disk_bytes": min((v for _,v in free_disk), default=None),
                           "sample_interval_seconds": 10,
                           "parts_with_resource_samples": sum(x["resource_sample_coverage"] for x in parts)}
    if result["s1"] != normalized_s1:
        raise RuntimeError("Full test S1 coverage mismatch")
    save_json(Path("work/test_candidate_audit.json"), result)
    pq.write_table(pa.Table.from_pylist([
        {k:v for k,v in row.items() if not isinstance(v, dict)} for row in parts
    ]), "work/test_candidate_part_metrics.parquet", compression="zstd")
    Path("work/test_candidate_audit.md").write_text(
        f"# Full test candidate structural audit\n\nPASS: {len(parts)} checksummed partitions, unique pair identities, valid target prefixes, provenance and rank bounds, and complete S1 coverage.\n\n"
        f"S1: {result['s1']:,}; candidates: {total:,}; S2: {result['s2']:,}; S3: {result['s3']:,}.\n",
        encoding="utf-8")
    return result


if __name__ == "__main__":
    result = run()
    print(json.dumps({k:v for k,v in result.items() if k != "partitions"}, indent=2), flush=True)
