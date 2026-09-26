"""Foundation item 4 — streaming, bounded-memory ingestion TSV -> parquet.

Reads a source TSV in fixed-size batches and writes a derived parquet carrying the raw
entity_id/name/address (never mutated) plus the deterministic normalized keys from
``er.normalize``. Peak memory is one batch, not the file, so 5M-row sources ingest under
the ~0.8 GB budget. The original TSVs remain the untouched system of record for raw text.

Reproducible command:
    PYTHONUTF8=1 PYTHONPATH=code/business_entity_resolution/src \
        .venv/Scripts/python -m er.io --data-dir dataset --which train
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BATCH = 100_000
_COLS = ["entity_id", "business_name", "business_address", "country",
         "name_norm", "name_nosuffix", "name_sorted", "name_acronym",
         "addr_norm", "num_tokens", "country_norm"]


def ingest_source(path: str, out_parquet: str) -> int:
    """Stream ``path`` -> ``out_parquet``; return row count. Bounded to one BATCH."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from er.normalize import normalize_record

    schema = pa.schema([(c, pa.string()) for c in _COLS])
    writer = pq.ParquetWriter(out_parquet, schema)
    buf: list[list[str]] = []
    n = 0

    def flush() -> None:
        if not buf:
            return
        cols = list(zip(*buf))
        writer.write_table(pa.table(
            {c: list(cols[i]) for i, c in enumerate(_COLS)}, schema=schema))
        buf.clear()

    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(rd)
        assert header == ["entity_id", "business_name", "business_address", "country"], header
        for row in rd:
            eid, name, addr, country = row[0], row[1], row[2], row[3]
            rec = normalize_record(name, addr, country)
            buf.append([eid, name, addr, country,
                        rec["name_norm"], rec["name_nosuffix"], rec["name_sorted"],
                        rec["name_acronym"], rec["addr_norm"],
                        ",".join(rec["num_tokens"]), rec["country_norm"]])
            n += 1
            if len(buf) >= BATCH:
                flush()
    flush()
    writer.close()
    return n


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="dataset")
    ap.add_argument("--which", choices=["train", "test", "both"], default="train")
    ap.add_argument("--out-dir", default="work/keys")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.makedirs(args.out_dir, exist_ok=True)

    splits = {"train": ["train"], "test": ["test"], "both": ["train", "test"]}[args.which]
    for sp in splits:
        for i in (1, 2, 3):
            src = f"{args.data_dir}/{sp}/{sp}_source{i}.tsv"
            out = f"{args.out_dir}/{sp}_s{i}.parquet"
            n = ingest_source(src, out)
            print(f"ingested {src} -> {out}  rows={n}")


if __name__ == "__main__":
    _main()
