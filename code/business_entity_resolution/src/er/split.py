"""Foundation item 3 — deterministic, seeded Source-1 train/val split + leakage analysis.

The split is defined ONLY over Source-1 entity_ids (S1 is the deduplicated reference).
Assignment is a pure hash of (seed, entity_id) via hashlib.md5 — reproducible across
runs, machines, and Python versions (unlike the salted built-in ``hash``). No labels are
read to decide the split, and no modeling decision in this repo may consult val labels.

Why splitting on S1 is leakage-safe for the matcher (proven, not assumed):
integrity_check.py established that every S2/S3 id is matched to AT MOST ONE S1
(cross-S1 sharing = 0). Therefore partitioning S1 into train/val also partitions the
labeled (S1, S2/S3) pairs with ZERO shared S2/S3 records across the boundary — a val
target record can never appear in a training pair. The only residual concern is
near-duplicate *distinct* S1 businesses straddling the split; we quantify that here as
cross-split collisions of the (country, name_nosuffix) key.

Reproducible command:
    PYTHONUTF8=1 PYTHONPATH=code/business_entity_resolution/src \
        .venv/Scripts/python -m er.split --data-dir dataset --val-frac 0.1 --seed 42
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SPLIT_VERSION = "1"


def assign(entity_id: str, seed: int, val_frac: float) -> str:
    """Deterministic bucket in [0,1) from md5(seed:id); < val_frac -> 'val'."""
    h = hashlib.md5(f"{seed}:{entity_id}".encode("utf-8")).hexdigest()
    bucket = int(h[:8], 16) / 0x100000000  # 32-bit -> [0,1)
    return "val" if bucket < val_frac else "train"


def _main() -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from er.normalize import NORM_VERSION, country_norm, name_nosuffix

    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="dataset")
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="work/split_s1.parquet")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.makedirs("work", exist_ok=True)

    src1 = f"{args.data_dir}/train/train_source1.tsv"
    schema = pa.schema([("entity_id", pa.string()), ("name_key", pa.string()),
                        ("country", pa.string()), ("split", pa.string())])
    writer = pq.ParquetWriter(args.out, schema)
    BATCH = 100_000
    buf: list[tuple[str, str, str, str]] = []
    n_train = n_val = 0

    def flush() -> None:
        if not buf:
            return
        cols = list(zip(*buf))
        writer.write_table(pa.table({"entity_id": list(cols[0]),
                                     "name_key": list(cols[1]),
                                     "country": list(cols[2]),
                                     "split": list(cols[3])}, schema=schema))
        buf.clear()

    with open(src1, encoding="utf-8", newline="") as f:
        rd = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(rd)
        assert header[:2] == ["entity_id", "business_name"], header
        for row in rd:
            eid, name, country = row[0], row[1], row[3]
            sp = assign(eid, args.seed, args.val_frac)
            n_val += sp == "val"
            n_train += sp == "train"
            buf.append((eid, name_nosuffix(name), country_norm(country), sp))
            if len(buf) >= BATCH:
                flush()
    flush()
    writer.close()

    # ---- leakage analysis (bounded, DuckDB out-of-core over the small parquet) ----
    import duckdb
    con = duckdb.connect()
    con.execute("SET memory_limit='512MB'; SET threads=4;")
    t = f"read_parquet('{args.out}')"
    total = n_train + n_val
    # keys present (as a distinct-S1 business key) on BOTH sides of the split
    coll_keys, coll_val_entities = con.sql(f"""
        WITH k AS (
          SELECT country, name_key,
                 count(*) FILTER (WHERE split='train') AS tr,
                 count(*) FILTER (WHERE split='val')   AS va
          FROM {t} WHERE length(name_key)>0 GROUP BY country, name_key)
        SELECT count(*) FILTER (WHERE tr>0 AND va>0),
               coalesce(sum(va) FILTER (WHERE tr>0 AND va>0), 0)
        FROM k""").fetchone()
    empty_key = con.sql(f"SELECT count(*) FROM {t} WHERE length(name_key)=0").fetchone()[0]

    manifest = [
        f"split_version={SPLIT_VERSION} norm_version={NORM_VERSION} "
        f"seed={args.seed} val_frac={args.val_frac}",
        f"S1 total={total}  train={n_train} ({100*n_train/total:.2f}%)  "
        f"val={n_val} ({100*n_val/total:.2f}%)",
        f"val target-record leakage across split: 0 by construction "
        f"(each S2/S3 matched to <=1 S1; see integrity_check.py)",
        f"cross-split (country,name_nosuffix) collision keys: {coll_keys}",
        f"  -> val entities sharing a name-key with a train entity: {coll_val_entities} "
        f"({100*coll_val_entities/max(n_val,1):.3f}% of val)",
        f"S1 rows with empty name_nosuffix key: {empty_key}",
    ]
    report = "# S1 train/val split + leakage\n\n" + "\n".join(f"- {m}" for m in manifest) + "\n"
    with open("work/split_manifest.md", "w", encoding="utf-8") as f:
        f.write(report)
    print(report)


if __name__ == "__main__":
    _main()
