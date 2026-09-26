"""Partitioned materialization of a frozen candidate policy."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import threading
import time

import duckdb

from .policies import CandidatePolicy, load_policy, source_quota_predicate


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
                ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended", ctypes.c_ulonglong)]


def free_ram() -> int:
    status = _MemoryStatus(); status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed")
    return status.avail_phys


class ResourceGuard:
    """Monitor whole-stage temp growth and interrupt DuckDB at safe limits."""
    def __init__(self, con, temp_dir: Path):
        self.con, self.temp_dir = con, temp_dir
        self.stop = threading.Event(); self.peak_temp = 0; self.violation = ""
        self.thread = threading.Thread(target=self._poll, daemon=True)

    def __enter__(self):
        ram, disk = free_ram(), shutil.disk_usage(self.temp_dir).free
        print(f"preflight free_ram={ram} free_disk={disk} duckdb=800MB threads=1 "
              "estimated_output_rows=34568979", flush=True)
        if ram < 1_350_000_000 or disk < 15_000_000_000:
            raise RuntimeError("Insufficient free RAM or temp disk")
        self.thread.start(); return self

    def _poll(self):
        while not self.stop.wait(0.5):
            try:
                growth = sum(p.stat().st_size for p in self.temp_dir.rglob("*") if p.is_file())
                self.peak_temp = max(self.peak_temp, growth)
                if free_ram() < 500_000_000 or shutil.disk_usage(self.temp_dir).free < 10_000_000_000:
                    self.violation = "Resource guard tripped"
                    self.con.interrupt(); return
            except Exception as exc:
                self.violation = f"Resource monitor failed: {exc}"
                self.con.interrupt(); return

    def __exit__(self, exc_type, exc, tb):
        self.stop.set(); self.thread.join(timeout=3)
        print(f"peak_temp_bytes={self.peak_temp} guard={self.violation or 'OK'}", flush=True)
        if self.violation and exc_type is None:
            raise RuntimeError(self.violation)


def _quoted_paths(paths: list[Path]) -> str:
    return ",".join("'" + p.as_posix() + "'" for p in paths)


def discover_inputs(root: Path) -> dict[str, object]:
    name = sorted(root.glob("rank_evidence_name_*.parquet"))
    address = sorted(root.glob("rank_evidence_addr_*.parquet"))
    if len(name) != 64 or len(address) != 64:
        raise RuntimeError(f"Expected 64 name and 64 address shards; found {len(name)}, {len(address)}")
    required = {k: root / v for k, v in {
        "sorted": "sorted.parquet", "exact_address": "exact_addr.parquet",
        "heavy_rank": "heavy_rank.parquet",
    }.items()}
    missing = [str(v) for v in required.values() if not v.exists()]
    if missing:
        raise FileNotFoundError(missing)
    return {**required, "name_ranks": name, "address_ranks": address}


def connect(temp_dir: Path, memory_limit: str = "800MB", threads: int = 1):
    temp_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{memory_limit}'; SET threads={threads};")
    con.execute("SET preserve_insertion_order=false")
    con.execute(f"SET temp_directory='{temp_dir.as_posix()}'")
    return con


def materialize_partition(con, policy: CandidatePolicy, inputs: dict[str, object],
                          shard: int, output: Path) -> int:
    pred = source_quota_predicate(policy)
    hf = f"hash(s1)%{policy.partitions}={shard}"
    pieces = [
        f"SELECT s1,mid,1 b FROM read_parquet('{inputs['sorted'].as_posix()}') WHERE {hf}",
        f"SELECT s1,mid,2 b FROM read_parquet('{inputs['exact_address'].as_posix()}') WHERE {hf}",
        f"SELECT r.s1,r.mid,4 b FROM read_parquet([{_quoted_paths(inputs['name_ranks'])}]) r WHERE {pred} AND hash(r.s1)%{policy.partitions}={shard}",
        f"SELECT r.s1,r.mid,8 b FROM read_parquet([{_quoted_paths(inputs['address_ranks'])}]) r WHERE {pred} AND hash(r.s1)%{policy.partitions}={shard}",
    ]
    union = " UNION ALL ".join(pieces)
    ranked = f"""SELECT s1,mid,bit_or(b)::UTINYINT prov FROM ({union}) GROUP BY 1,2"""
    if policy.heavy_sorted_cap is not None:
        ranked = f"""SELECT u.s1,u.mid,u.prov FROM ({ranked}) u
            LEFT JOIN read_parquet('{inputs['heavy_rank'].as_posix()}') h
              ON u.s1=h.s1 AND u.mid=h.mid
            WHERE h.rk IS NULL OR NOT (u.prov=1 AND h.rk>{policy.heavy_sorted_cap})"""
    partial = output.with_suffix(".partial.parquet")
    partial.unlink(missing_ok=True)
    con.execute(f"""COPY (SELECT s1 source1_entity_id,mid target_entity_id,prov
        FROM ({ranked}) ORDER BY 1,2) TO '{partial.as_posix()}'
        (FORMAT PARQUET, COMPRESSION ZSTD)""")
    os.replace(partial, output)
    return con.sql(f"SELECT count(*) FROM read_parquet('{output.as_posix()}')").fetchone()[0]


def materialize(policy_path: Path, rank_root: Path, output_dir: Path,
                resume: bool = True) -> dict:
    policy = load_policy(policy_path)
    inputs = discover_inputs(rank_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    con = connect(output_dir / "tmp")
    started = time.time(); rows = []
    try:
        with ResourceGuard(con, output_dir / "tmp") as guard:
            for shard in range(policy.partitions):
                out = output_dir / f"part-{shard:02d}.parquet"
                if resume and out.exists():
                    try:
                        n = con.sql(f"SELECT count(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0]
                        rows.append((out, n)); continue
                    except Exception:
                        out.rename(output_dir / f"invalid-part-{shard:02d}.parquet")
                rows.append((out, materialize_partition(con, policy, inputs, shard, out)))
    finally:
        con.close()
    result = {"policy_id": policy.policy_id, "parts": len(rows),
              "rows": sum(n for _, n in rows), "wall_seconds": time.time()-started,
              "peak_temp_bytes": guard.peak_temp,
              "files": [p.as_posix() for p, _ in rows]}
    (output_dir / "materialization.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--rank-root", default="work/freeze_gate", type=Path)
    ap.add_argument("--output-dir", default="work/final_candidate_policy_parts", type=Path)
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()
    print(json.dumps(materialize(args.config,args.rank_root,args.output_dir,not args.no_resume),indent=2))


if __name__ == "__main__":
    main()
