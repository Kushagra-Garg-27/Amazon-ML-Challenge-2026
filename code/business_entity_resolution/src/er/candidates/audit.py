"""Candidate artifact integrity, direct-GT evaluation, and manifests."""
from __future__ import annotations

import hashlib
import argparse
import json
from pathlib import Path

import duckdb


def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(8<<20),b""):
            h.update(block)
    return h.hexdigest()


def partition_manifest(parts: list[Path]) -> dict:
    entries=[]
    con=duckdb.connect()
    for path in sorted(parts):
        n=con.sql(f"SELECT count(*) FROM read_parquet('{path.as_posix()}')").fetchone()[0]
        entries.append({"path":path.as_posix(),"rows":n,"bytes":path.stat().st_size,
                        "sha256":sha256_file(path)})
    con.close()
    logical=hashlib.sha256()
    for e in entries:
        logical.update(f"{Path(e['path']).name}:{e['rows']}:{e['sha256']}\n".encode())
    return {"parts":entries,"row_count":sum(e["rows"] for e in entries),
            "artifact_bytes":sum(e["bytes"] for e in entries),
            "partition_manifest_sha256":logical.hexdigest()}


def compare_identity(reference: Path, parts: list[Path], memory_limit="800MB") -> dict:
    con=duckdb.connect(); con.execute(f"SET memory_limit='{memory_limit}'; SET threads=1")
    paths=",".join("'"+p.as_posix()+"'" for p in sorted(parts))
    ref=reference.as_posix()
    added=con.sql(f"""SELECT count(*) FROM read_parquet([{paths}]) p
        ANTI JOIN read_parquet('{ref}') r ON p.source1_entity_id=r.source1_entity_id
        AND p.target_entity_id=r.target_entity_id""").fetchone()[0]
    removed=con.sql(f"""SELECT count(*) FROM read_parquet('{ref}') r
        ANTI JOIN read_parquet([{paths}]) p ON p.source1_entity_id=r.source1_entity_id
        AND p.target_entity_id=r.target_entity_id""").fetchone()[0]
    duplicates=con.sql(f"""SELECT count(*)-count(DISTINCT
        (source1_entity_id,target_entity_id)) FROM read_parquet([{paths}])""").fetchone()[0]
    con.close()
    return {"added":added,"removed":removed,"duplicates":duplicates,"equal":added==removed==duplicates==0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", required=True, type=Path)
    ap.add_argument("--parts", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    parts = sorted(args.parts.glob("part-*.parquet"))
    if not parts:
        raise RuntimeError(f"No parts found under {args.parts}")
    result = partition_manifest(parts)
    result["identity_comparison"] = compare_identity(args.reference, parts)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"row_count": result["row_count"],
                      "artifact_bytes": result["artifact_bytes"],
                      **result["identity_comparison"]}, indent=2))


if __name__ == "__main__":
    main()
