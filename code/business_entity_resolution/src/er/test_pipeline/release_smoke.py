"""Bounded, label-free end-to-end test release smoke gate."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import duckdb
import pyarrow.parquet as pq

from er.candidates.pilot import materialize_group
from er.features.materialize import materialize_part, ARROW_SCHEMA
from er.test_pipeline import assemble, score
from er.test_pipeline.common import sha256


def main() -> None:
    root = Path("work/test_smoke")
    root.mkdir(parents=True, exist_ok=True)
    selection = root / "selection.parquet"
    c = duckdb.connect()
    c.execute("SET memory_limit='700MB'; SET threads=1")
    (root / "tmp").mkdir(exist_ok=True)
    c.execute(f"SET temp_directory='{(root / 'tmp').as_posix()}'")
    if not selection.exists():
        partial = selection.with_suffix(".partial.parquet")
        c.execute(f"""COPY (WITH k AS (
          SELECT entity_id,country_norm,addr_norm,name_sorted,
            row_number() OVER(PARTITION BY country_norm ORDER BY hash(entity_id),entity_id) rn,
            row_number() OVER(PARTITION BY country_norm,addr_norm='' ORDER BY hash(entity_id),entity_id) missing_rn
          FROM read_parquet('work/keys/test_s1.parquet'))
          SELECT DISTINCT entity_id,'smoke' split,country_norm
          FROM k WHERE rn<=20 OR (addr_norm='' AND missing_rn<=10)
          ORDER BY country_norm,entity_id)
          TO '{partial.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        os.replace(partial, selection)
    countries = [r[0] for r in c.execute(f"SELECT DISTINCT country_norm FROM read_parquet('{selection.as_posix()}') ORDER BY 1").fetchall()]
    assert {"india", "us", "france"}.issubset(countries)
    c.close()
    candidate_dir = root / "candidates"
    feature_dir = root / "features"
    candidate_dir.mkdir(exist_ok=True)
    feature_dir.mkdir(exist_ok=True)
    groups = []
    for country in countries:
        candidate = candidate_dir / f"smoke_{country}.parquet"
        metrics = materialize_group(selection, "smoke", country, candidate,
                                    keys_prefix="test", df_root=Path("work/test_rank"),
                                    rank_output=root / "rank")
        features = feature_dir / candidate.name
        feature_metrics = materialize_part(candidate, features, keys_prefix="test",
                                           temp_dir=root / "feature_tmp")
        if pq.read_schema(features).names != ARROW_SCHEMA.names:
            raise RuntimeError("Feature smoke schema mismatch")
        if metrics["candidates"] != feature_metrics["rows"]:
            raise RuntimeError("Candidate/feature identity count mismatch")
        groups.append({"country": country, "s1": metrics["s1"],
                       "candidates": metrics["candidates"], "feature_rows": feature_metrics["rows"]})
    scored = score.run(feature_dir, root / "scores")
    frag_dir = root / "assembly"
    for scored_part in sorted((root / "scores").glob("*.parquet")):
        assemble.fragment(scored_part, selection, frag_dir)
    out = root / "output"
    candidates = assemble.merge(frag_dir, out / "candidate_pairs.tsv", "candidate")
    matches = assemble.merge(frag_dir, out / "matching_results.tsv", "matching")
    sample_dir = root / "fixture_test"
    sample_dir.mkdir(exist_ok=True)
    header = "entity_id\tbusiness_name\tbusiness_address\tcountry\n"
    ids = {2: set(), 3: set()}
    s1 = []
    with (out / "candidate_pairs.tsv").open(encoding="utf-8") as f:
        next(f)
        for line in f:
            entity, values = line.rstrip("\n").split("\t")
            s1.append(entity)
            for target in values.split(",") if values else ():
                ids[int(target[1])].add(target)
    with (sample_dir / "test_source1.tsv").open("w", encoding="utf-8", newline="") as f:
        f.write(header)
        for entity in s1:
            f.write(f"{entity}\t\t\t\n")
    for source in (2, 3):
        with (sample_dir / f"test_source{source}.tsv").open("w", encoding="utf-8", newline="") as f:
            f.write(header)
            for entity in sorted(ids[source]):
                f.write(f"{entity}\t\t\t\n")
    command = [sys.executable, "-m", "er.test_pipeline.official_validator", "--matching",
               str(out / "matching_results.tsv"), "--candidate",
               str(out / "candidate_pairs.tsv"), "--test-dir", str(sample_dir), "--check-ids"]
    validation = subprocess.run(command, capture_output=True, text=True)
    if validation.returncode or "PASS" not in validation.stdout:
        raise RuntimeError(validation.stdout + validation.stderr)
    summary = {"status": "PASS", "groups": groups, "score": scored,
               "candidate_output": candidates, "matching_output": matches,
               "validator_command": command, "validator_stdout": validation.stdout,
               "validator_stderr": validation.stderr, "validator_exit": validation.returncode,
               "selection_sha256": sha256(selection)}
    (root / "smoke_result.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
