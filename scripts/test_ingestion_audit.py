"""Independent raw-to-normalized integrity and normalization replay audit."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

from er.normalize import normalize_record
from er.io import _COLS


def main() -> None:
    c = duckdb.connect()
    c.execute("SET memory_limit='700MB'; SET threads=1")
    temp = Path("work/test_ingestion_audit_tmp")
    temp.mkdir(parents=True, exist_ok=True)
    c.execute(f"SET temp_directory='{temp.as_posix()}'")
    results = {}
    for source in (1, 2, 3):
        raw = f"read_csv('dataset/test/test_source{source}.tsv',delim='\\t',header=true,all_varchar=true,quote='')"
        norm = f"read_parquet('work/keys/test_s{source}.parquet')"
        schema = [x[0] for x in c.execute(f"DESCRIBE SELECT * FROM {norm}").fetchall()]
        if schema != _COLS:
            raise RuntimeError(f"Normalized S{source} schema mismatch")
        n, distinct_ids, missing_name, missing_addr = c.execute(f"""SELECT count(*),
          count(DISTINCT entity_id),
          count(*) FILTER(WHERE business_name IS NULL OR business_name=''),
          count(*) FILTER(WHERE business_address IS NULL OR business_address='') FROM {norm}""").fetchone()
        raw_n, raw_name, raw_addr = c.execute(f"""SELECT count(*),
          count(*) FILTER(WHERE business_name IS NULL OR business_name=''),
          count(*) FILTER(WHERE business_address IS NULL OR business_address='') FROM {raw}""").fetchone()
        country_norm = dict(c.execute(f"SELECT country,count(*) FROM {norm} GROUP BY 1 ORDER BY 1").fetchall())
        country_raw = dict(c.execute(f"SELECT country,count(*) FROM {raw} GROUP BY 1 ORDER BY 1").fetchall())
        missing_ids = c.execute(f"SELECT count(*) FROM (SELECT entity_id FROM {raw} EXCEPT SELECT entity_id FROM {norm})").fetchone()[0]
        extra_ids = c.execute(f"SELECT count(*) FROM (SELECT entity_id FROM {norm} EXCEPT SELECT entity_id FROM {raw})").fetchone()[0]
        partition_paths = sorted(Path("work/test_keys").glob(f"s{source}_*.parquet"))
        if len(partition_paths) != 48:
            raise RuntimeError(f"Expected 48 normalized S{source} partitions, got {len(partition_paths)}")
        partitioned = c.execute(f"""SELECT count(*),count(DISTINCT entity_id),
          count(*) FILTER(WHERE hash(entity_id)%16 != cast(regexp_extract(filename,'_([0-9]{{2}})\\.parquet$',1) as integer))
          FROM read_parquet('work/test_keys/s{source}_*.parquet',filename=true)""").fetchone()
        if (n, missing_name, missing_addr, country_norm) != (raw_n, raw_name, raw_addr, country_raw) or n != distinct_ids or missing_ids or extra_ids or partitioned != (n,n,0):
            raise RuntimeError(f"Normalized S{source} raw integrity mismatch")
        sample = c.execute(f"""SELECT entity_id,business_name,business_address,country,
          name_norm,name_nosuffix,name_sorted,name_acronym,addr_norm,num_tokens,country_norm
          FROM {norm} ORDER BY hash(entity_id),entity_id LIMIT 100""").fetchall()
        for eid, name, addr, country, *actual in sample:
            rec = normalize_record(name, addr, country)
            if rec != normalize_record(name, addr, country):
                raise RuntimeError(f"Repeated normalization changed: {eid}")
            expected = [rec["name_norm"], rec["name_nosuffix"], rec["name_sorted"],
                        rec["name_acronym"], rec["addr_norm"], ",".join(rec["num_tokens"]),
                        rec["country_norm"]]
            if actual != expected:
                raise RuntimeError(f"Non-deterministic normalization: {eid}")
        results[f"s{source}"] = {"rows": n, "unique_ids": distinct_ids,
                                 "missing_name": missing_name, "missing_address": missing_addr,
                                 "countries": country_norm, "missing_ids": missing_ids,
                                 "extra_ids": extra_ids, "partitioned_rows": partitioned[0],
                                 "partitions": len(partition_paths), "replayed_sample": len(sample)}
        print(f"S{source}: PASS {n} rows", flush=True)
    c.close()
    h = hashlib.sha256(Path("code/business_entity_resolution/src/er/normalize.py").read_bytes()).hexdigest()
    if h != "b3508b600e05e6008ceb7872d7b48e715df935ee7c4bb586d1cd7d5ec02ccedd":
        raise RuntimeError("Frozen normalization checksum mismatch")
    result = {"status": "PASS", "normalization_sha256": h, "sources": results}
    Path("work/test_ingestion_audit.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    Path("work/test_ingestion_audit.md").write_text("# Test ingestion and normalization audit\n\nPASS\n\n```json\n" + json.dumps(result, indent=2) + "\n```\n", encoding="utf-8")


if __name__ == "__main__":
    main()
