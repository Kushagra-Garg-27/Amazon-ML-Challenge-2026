"""Release-only read-only gate for immutable inputs and raw test TSVs."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import tempfile
import subprocess
import re
from datetime import datetime, timezone

import duckdb
import lightgbm

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "work/final_candidate_policy.json": "46fd324b3d4db7dbdf5fa4b8ce528f681335ce16f759fb93a39ba5b98a040fea",
    "work/feature_spec_v1_1.json": "37982fd6377aa2e36fe1c5dec0484a35c02ba4bcc4e3b67b3a337b33597f6e1f",
    "work/final_matcher_model.txt": "76ff78a7cc97e40174b6d631a6eaa062c786f0480c6693fd6387e90be6761d21",
    "work/final_matcher_policy.json": "ced4ee4133110f5f55c184c3d4020b5087ecac5398bdf9ebfd03f76b3662aba3",
    "work/final_eval_release_gate_config.json": "17165a4c083e73c747d5b57ca9e758959b0a8609d7306d738ad7ecb97326c6e9",
    "code/business_entity_resolution/src/er/normalize.py": "b3508b600e05e6008ceb7872d7b48e715df935ee7c4bb586d1cd7d5ec02ccedd",
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def memory_available() -> int:
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
                    ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
                    ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                    ("avail_extended_virtual", ctypes.c_ulonglong)]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed")
    return status.avail_phys


def main() -> None:
    os.chdir(ROOT)
    result = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "checks": {}, "raw": {}}
    checks = result["checks"]
    checks["hashes"] = {p: {"expected": want, "actual": sha(Path(p))} for p, want in EXPECTED.items()}
    if any(x["actual"] != x["expected"] for x in checks["hashes"].values()):
        raise RuntimeError("Frozen checksum mismatch")
    decision = json.loads(Path("work/final_eval_release_decision.json").read_text())
    checks["release_decision"] = decision["status"]
    if decision["status"] != "PASS" or not decision["test_inference_permitted"]:
        raise RuntimeError("Release decision is not PASS")
    policy = json.loads(Path("work/final_matcher_policy.json").read_text())
    feature_spec = json.loads(Path("work/feature_spec_v1_1.json").read_text())
    checks["python"] = platform.python_version()
    checks["lightgbm"] = lightgbm.__version__
    checks["feature_order_length"] = len(policy["feature_order"])
    checks["threshold"] = policy["threshold"]
    if checks["python"] != "3.12.10" or checks["lightgbm"] != "4.7.0":
        raise RuntimeError("Environment version mismatch")
    if len(policy["feature_order"]) != 61 or policy["threshold"] != 0.61:
        raise RuntimeError("Frozen matcher policy mismatch")
    booster = lightgbm.Booster(model_file="work/final_matcher_model.txt")
    checks["model_feature_order_matches"] = list(booster.feature_name()) == policy["feature_order"]
    if not checks["model_feature_order_matches"]:
        raise RuntimeError("Frozen model feature order mismatch")
    candidate_policy = json.loads(Path("work/final_candidate_policy.json").read_text())["policy"]
    checks["candidate_policy"] = {"s2_quota": candidate_policy["source_quotas"]["S2"],
                                  "s3_quota": candidate_policy["source_quotas"]["S3"],
                                  "heavy_cap": candidate_policy["heavy_sorted"]["cap"],
                                  "df": candidate_policy["df_thresholds"],
                                  "hash_partitions": candidate_policy["partitioning"]["hash_partitions"]}
    if checks["candidate_policy"] != {"s2_quota": 50, "s3_quota": 50, "heavy_cap": 100,
                                       "df": {"name": 2000, "address": 2000},
                                       "hash_partitions": 16}:
        raise RuntimeError("Frozen candidate policy mismatch")
    checks["feature_spec_version"] = feature_spec.get("version", feature_spec.get("feature_spec_version"))
    checks["free_disk_bytes"] = shutil.disk_usage(ROOT).free
    checks["free_ram_bytes"] = memory_available()
    if checks["free_disk_bytes"] < 48_000_000_000 or checks["free_ram_bytes"] < 1_350_000_000:
        raise RuntimeError("Resource guard failed")
    with tempfile.NamedTemporaryFile(prefix="release_preflight_", dir=ROOT / "work", delete=True) as f:
        f.write(b"test"); f.flush(); f.seek(0)
        checks["temp_write_read_ok"] = f.read() == b"test"
    if not checks["temp_write_read_ok"]:
        raise RuntimeError("Temporary-directory read/write failed")
    process_lines = subprocess.check_output(["tasklist", "/fo", "csv", "/nh"], text=True)
    checks["competing_python_duckdb_processes"] = [line for line in process_lines.splitlines()
        if re.match(r'^"(?:python|duckdb)', line, flags=re.IGNORECASE)
        and f'"{os.getpid()}"' not in line
        and f'"{os.getppid()}"' not in line]
    if checks["competing_python_duckdb_processes"]:
        raise RuntimeError("Competing Python/DuckDB process")

    c = duckdb.connect()
    c.execute("SET memory_limit='700MB'")
    c.execute("SET threads=1")
    c.execute("SET temp_directory='work/test_preflight_tmp'")
    for i in (1, 2, 3):
        path = Path(f"dataset/test/test_source{i}.tsv")
        with path.open(encoding="utf-8", newline="") as f:
            header = next(csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE))
        if header != ["entity_id", "business_name", "business_address", "country"]:
            raise RuntimeError(f"Invalid schema: {path}: {header}")
        source = f"read_csv('{path.as_posix()}', delim='\\t', header=true, all_varchar=true, quote='')"
        q = f"""SELECT count(*) n, count(DISTINCT entity_id) distinct_ids,
          count(*) FILTER (WHERE entity_id NOT LIKE 'S{i}-%') bad_prefix,
          count(*) FILTER (WHERE business_name IS NULL OR business_name='') name_missing,
          count(*) FILTER (WHERE business_address IS NULL OR business_address='') address_missing
          FROM {source}"""
        n, distinct_ids, bad_prefix, name_missing, address_missing = c.execute(q).fetchone()
        countries = dict(c.execute(f"SELECT country,count(*) FROM {source} GROUP BY 1 ORDER BY 1").fetchall())
        result["raw"][f"s{i}"] = {"path": str(path), "rows": n, "distinct_ids": distinct_ids,
                                  "bad_prefix": bad_prefix, "name_missing": name_missing,
                                  "address_missing": address_missing, "countries": countries}
        print(f"S{i}: {n} rows, {distinct_ids} distinct IDs, {countries}", flush=True)
        if n != distinct_ids or bad_prefix:
            raise RuntimeError(f"Test S{i} ID integrity failure")
    c.close()
    checks["s2_s3_namespace_disjoint"] = True
    suite_log = Path("work/test_release_preflight_suite.log")
    if suite_log.exists():
        content = suite_log.read_text(encoding="utf-8", errors="replace")
        matched = re.search(r"Ran (\d+) tests in ([0-9.]+)s", content)
        checks["existing_test_suite"] = {"command": ".venv\\Scripts\\python.exe -m unittest discover -s code/business_entity_resolution/tests -v",
                                         "count": int(matched.group(1)) if matched else None,
                                         "passed": bool(matched and re.search(r"\nOK\s*$", content)),
                                         "log": suite_log.as_posix()}
        if not checks["existing_test_suite"]["passed"]:
            raise RuntimeError("Existing test suite did not pass")
    result["status"] = "PASS"
    out = Path("work/test_release_preflight.json")
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    Path("work/test_release_preflight.md").write_text(
        "# Test release preflight\n\nPASS: frozen hashes, release decision, Python/LightGBM, "
        "feature order/threshold, resource guard, temp write, raw schemas, unique IDs and namespaces.\n\n"
        + "```json\n" + json.dumps(result, indent=2) + "\n```\n", encoding="utf-8")
    print(out, flush=True)


if __name__ == "__main__":
    main()
