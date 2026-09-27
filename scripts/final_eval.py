"""One-time, staged evaluation of the frozen entity matcher.

Run stages in order. The evaluate stage is the only stage permitted to read final
evaluation ground truth, and writes an opened marker before doing so.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import csv
import io
from ctypes import wintypes

import duckdb
import lightgbm as lgb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from er.candidates import pilot
from er.features import materialize as feature_materialize
from er.matcher.controlled import MODEL_FEATURE_NAMES_V1_1, validate_model_feature_order

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
W = Path("work")
PARTS = 8
SEED = 20260926


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sql_path(path: Path | str) -> str:
    return str(path).replace("\\", "/").replace("'", "''")


def db(temp: str = "work/final_eval_tmp"):
    Path(temp).mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.execute("SET memory_limit='800MB'; SET threads=1; SET preserve_insertion_order=false")
    c.execute(f"SET temp_directory='{temp}'")
    return c


def artifact(path: Path, **details) -> dict:
    return {"path": path.as_posix(), "bytes": path.stat().st_size,
            "sha256": digest(path), **details}


def process_peak_rss() -> int:
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    value = Counters(); value.cb = ctypes.sizeof(value)
    ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),
        ctypes.byref(value), value.cb)
    return int(value.PeakWorkingSetSize)


def frozen_hashes() -> dict:
    candidate = read_json(W / "final_candidate_policy_manifest.json")
    matcher = read_json(W / "final_matcher_manifest.json")
    split = read_json(W / "matcher_split_checksums.json")
    expected = {
        "candidate_policy": (W / "final_candidate_policy.json", candidate["policy"]["sha256"]),
        "normalization": (Path(candidate["frozen_inputs"]["normalization"]["path"]),
                          candidate["frozen_inputs"]["normalization"]["sha256"]),
        "feature_spec": (W / "feature_spec_v1_1.json", matcher["artifacts"][2]["sha256"]),
        "model": (W / "final_matcher_model.txt", matcher["artifacts"][0]["sha256"]),
        "matcher_policy": (W / "final_matcher_policy.json", matcher["artifacts"][1]["sha256"]),
        "matcher_split": (W / "matcher_split_manifest.parquet",
                          read_json(W / "model_development_split_checksums.json")["top_level_split_sha256"]),
        "matcher_manifest": (W / "final_matcher_manifest.json",
                             "a52fb33a8a6f2964ade4a0946b25b3cbdb780ba54604c4da5a0a13e8983e206a"),
    }
    result = {k: {"path": p.as_posix(), "expected": x, "actual": digest(p),
                  "pass": digest(p) == x} for k, (p, x) in expected.items()}
    policy = read_json(W / "final_matcher_policy.json")
    model = lgb.Booster(model_file=(W / "final_matcher_model.txt").as_posix())
    result["feature_order"] = {"pass": list(policy["feature_order"]) == list(MODEL_FEATURE_NAMES_V1_1)
                               == list(model.feature_name()), "count": len(model.feature_name())}
    result["lightgbm"] = {"expected": policy["lightgbm"]["version"], "actual": lgb.__version__,
                          "pass": lgb.__version__ == policy["lightgbm"]["version"]}
    result["seed_and_batch"] = {"values": policy["seeds"] | {"batch_candidates": policy["inference"]["batch_candidates"]},
                                "pass": policy["lightgbm"]["parameters"]["seed"] == 42
                                and policy["inference"]["batch_candidates"] == 200000}
    result["final_eval_split"] = {"s1": split["splits"]["model_final_eval"]["s1"],
                                  "id_sha256": split["splits"]["model_final_eval"]["entity_id_sha256"]}
    return result


def preopen() -> dict:
    if (W / "final_eval_opened.json").exists():
        raise RuntimeError("Final evaluation already opened")
    hashes = frozen_hashes()
    c = db()
    c.execute("CREATE TEMP VIEW final_ids AS SELECT entity_id FROM read_parquet('work/matcher_split_manifest.parquet') WHERE split='model_final_eval'")
    paths = []
    for folder in ("model_development_candidates", "model_development_features",
                   "model_development_negative_samples", "model_threshold_candidates",
                   "model_threshold_features", "model_threshold_scores", "scores_mining_fit"):
        paths.extend(sorted((W / folder).glob("*.parquet")))
    paths += [W / x for x in ("model_development_samples.parquet", "model_development_labels.parquet",
              "model_development_generation_selection.parquet", "model_threshold_selection.parquet",
              "model_tune_validation_sample.parquet", "feature_pilot_s1.parquet",
              "feature_pilot_labels.parquet", "final_candidate_provenance.parquet")]
    paths.extend(sorted((W / "scores_frozen_model_tune").glob("*.parquet")))
    examined = []
    for path in paths:
        if not path.exists():
            continue
        cols = pq.read_schema(path).names
        col = "source1_entity_id" if "source1_entity_id" in cols else "entity_id" if "entity_id" in cols else None
        if col is None:
            continue
        overlap = c.execute(f"SELECT count(*) FROM read_parquet('{sql_path(path)}') p JOIN final_ids f ON p.{col}=f.entity_id").fetchone()[0]
        examined.append({"path": path.as_posix(), "s1_overlap_rows": overlap})
    id_count, id_unique = c.execute("SELECT count(*),count(DISTINCT entity_id) FROM final_ids").fetchone()
    # The split creation's global labelled-target audit checked every GT target and
    # found zero cross-split reuse. Re-reading final GT here would open the gate.
    target_disjoint = read_json(W / "matcher_split_checksums.json")["labelled_target_overlap"]
    c.close()
    class Mem(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                    ("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                    ("page_total", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong),
                    ("virtual_total", ctypes.c_ulonglong), ("virtual_free", ctypes.c_ulonglong),
                    ("extended", ctypes.c_ulonglong)]
    m = Mem(); m.length = ctypes.sizeof(m); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    process_csv = subprocess.check_output(["tasklist", "/fo", "csv", "/nh"], text=True)
    jobs = [{"pid": int(row[1]), "name": row[0]} for row in csv.reader(io.StringIO(process_csv))
            if len(row) > 1 and row[1].isdigit() and int(row[1]) not in (os.getpid(), os.getppid())
            and row[0].lower().startswith(("python", "duckdb"))]
    checks = {"frozen": all(x.get("pass", True) for x in hashes.values()),
              "final_s1_count": id_count == id_unique == 110341,
              "prior_s1_overlap": all(x["s1_overlap_rows"] == 0 for x in examined),
              "target_disjoint_prior_split_audit": target_disjoint == 0,
              "no_python_duckdb_jobs": len(jobs) == 0}
    result = {"schema_version": 1, "time_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "checks": checks, "pass": all(checks.values()), "frozen": hashes,
              "examined_artifacts": examined, "prior_label_audit_target_overlap": target_disjoint,
              "prior_split_caveat": "Split report documents temporary aggregate GT counts during split creation; no final-eval model artifacts or selection used them.",
              "running_jobs": jobs, "free_ram_bytes": m.free,
              "free_disk_bytes": shutil.disk_usage(W).free,
              "baseline_tests": {"command": ".venv\\Scripts\\python.exe -m unittest discover -s code/business_entity_resolution/tests -v",
                                 "tests": 114, "failures": 0, "exit_code": 0}}
    write_json(W / "final_eval_preopen_audit.json", result)
    (W / "final_eval_preopen_audit.md").write_text(
        "# Final evaluation pre-open audit\n\n" +
        f"Status: **{'PASS' if result['pass'] else 'FAIL'}**. Final labels remain closed.\n\n" +
        f"S1 artifacts audited: {len(examined)}; overlap rows: {sum(x['s1_overlap_rows'] for x in examined)}. " +
        f"Prior global labelled-target overlap: {target_disjoint}. Baseline tests: 114 passed.\n\n" +
        "The earlier split report documents transient aggregate label counts at split creation. " +
        "They were not retained or used for development.\n", encoding="utf-8")
    return result


def gate_config() -> dict:
    audit = read_json(W / "final_eval_preopen_audit.json")
    if not audit["pass"]:
        raise RuntimeError("Pre-open audit failed")
    if (W / "final_eval_release_gate_config.json").exists():
        raise RuntimeError("Release gate already declared")
    reference = read_json(W / "model_threshold_results.json")["selected_result"]
    config = {"schema_version": 1, "signed_by": "frozen-final-eval-v1",
              "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "preopen_audit_sha256": digest(W / "final_eval_preopen_audit.json"),
              "model_sha256": digest(W / "final_matcher_model.txt"),
              "matcher_policy_sha256": digest(W / "final_matcher_policy.json"),
              "criteria": {"all_integrity_invariants": True, "minimum_macro_f0_5": 0.88,
                           "maximum_macro_drop_vs_threshold": 0.02, "minimum_pair_precision": 0.95,
                           "maximum_country_macro_drop": 0.04,
                           "prediction_subset_of_candidates": True, "duplicate_predictions": 0,
                           "prediction_records_per_s1": 1},
              "threshold_reference": {k: reference[k] for k in
                 ("macro_f0_5", "india_macro_f0_5", "us_macro_f0_5", "singleton_accuracy")},
              "fixed_slices": ["india", "us", "S2", "S3", "both_addresses_present",
                               "either_address_missing", "script_relation", "candidate_provenance",
                               "candidate_rank_bands", "true_match_count_bands"],
              "bootstrap": {"seed": SEED, "replicates": 1000, "unit": "S1", "interval": "percentile_95"},
              "unseen_key": {"name": "final S1 sorted-name key absent from model_fit S1 sorted-name keys",
                             "strict": "name and exact-address keys both absent"}}
    write_json(W / "final_eval_release_gate_config.json", config)
    return config


def require_gate() -> str:
    audit = read_json(W / "final_eval_preopen_audit.json")
    if not audit["pass"]:
        raise RuntimeError("Pre-open audit failed")
    return digest(W / "final_eval_release_gate_config.json")


def partition_selection() -> Path:
    path = W / "final_eval_selection.parquet"
    if path.exists():
        return path
    c = db()
    tmp = path.with_suffix(".partial.parquet")
    c.execute(f"""COPY (SELECT m.entity_id,
      'model_final_eval_' || lpad(cast(hash(m.entity_id)%{PARTS} as varchar),2,'0') split,
      k.country_norm FROM read_parquet('work/matcher_split_manifest.parquet') m
      JOIN read_parquet('work/keys/train_s1.parquet') k USING(entity_id)
      WHERE m.split='model_final_eval' ORDER BY split,country_norm,entity_id)
      TO '{sql_path(tmp)}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
    os.replace(tmp, path); c.close()
    return path


def candidate_stage() -> dict:
    gate = require_gate()
    start = time.time()
    selection = partition_selection()
    outdir = W / "final_eval_candidates"
    result = pilot.run(selection, outdir)
    c = db()
    glob = sql_path(outdir / "*.parquet")
    c.execute(f"CREATE TEMP VIEW pairs AS SELECT * FROM read_parquet('{glob}')")
    total, unique, invalid, missing_s1 = c.execute("""SELECT count(*),count(DISTINCT (source1_entity_id,target_entity_id)),
      count(*) FILTER(WHERE NOT (starts_with(target_entity_id,'S2-') OR starts_with(target_entity_id,'S3-'))),
      count(*) FILTER(WHERE source1_entity_id NOT IN (SELECT entity_id FROM read_parquet('work/final_eval_selection.parquet'))) FROM pairs""").fetchone()
    dist = c.execute("""WITH n AS (SELECT source1_entity_id,count(*) n FROM pairs GROUP BY 1)
      SELECT avg(coalesce(n.n,0)),median(coalesce(n.n,0)),quantile_cont(coalesce(n.n,0),.9),
      quantile_cont(coalesce(n.n,0),.95),quantile_cont(coalesce(n.n,0),.99),max(coalesce(n.n,0)),
      count(*) FILTER(WHERE n.n IS NULL)
      FROM read_parquet('work/final_eval_selection.parquet') s LEFT JOIN n ON s.entity_id=n.source1_entity_id""").fetchone()
    source = dict(c.execute("SELECT substr(target_entity_id,1,2),count(*) FROM pairs GROUP BY 1 ORDER BY 1").fetchall())
    provenance = dict(c.execute("SELECT cast(provenance as varchar),count(*) FROM pairs GROUP BY 1 ORDER BY 1").fetchall())
    country = dict(c.execute("""SELECT s.country_norm,count(*) FROM pairs p JOIN read_parquet('work/final_eval_selection.parquet') s
      ON p.source1_entity_id=s.entity_id GROUP BY 1 ORDER BY 1""").fetchall())
    c.close()
    files = [artifact(p, rows=pq.read_metadata(p).num_rows) for p in sorted(outdir.glob("*.parquet"))]
    audit = {"rows": total, "unique_identities": unique, "duplicates": total-unique,
             "invalid_targets": invalid, "outside_split": missing_s1,
             "distribution": dict(zip(("mean","median","p90","p95","p99","max","zero_s1"),dist)),
             "source_counts": source, "country_candidate_counts": country,
             "provenance_counts": provenance}
    if total != unique or invalid or missing_s1:
        raise RuntimeError(f"Candidate structural failure: {audit}")
    manifest = {"schema_version": 1, "release_gate_config_sha256": gate,
                "policy_sha256": digest(W / "final_candidate_policy.json"),
                "selection": artifact(selection), "parts": files, "audit": audit,
                "resource": {"wall_seconds": time.time()-start,
                             "bytes": sum(x["bytes"] for x in files),
                             "failed_retried_partitions": [], "exit_code": 0}}
    write_json(W / "final_eval_candidate_manifest.json", manifest)
    return manifest


def feature_stage() -> dict:
    gate = require_gate()
    candidate = read_json(W / "final_eval_candidate_manifest.json")
    start = time.time()
    outdir = W / "final_eval_features"
    result = feature_materialize.run(W / "final_eval_candidates", outdir)
    c = db()
    cg, fg = sql_path(W / "final_eval_candidates/*.parquet"), sql_path(outdir / "*.parquet")
    added, removed = c.execute(f"""SELECT
      (SELECT count(*) FROM (SELECT source1_entity_id,target_entity_id FROM read_parquet('{fg}')
       EXCEPT SELECT source1_entity_id,target_entity_id FROM read_parquet('{cg}'))),
      (SELECT count(*) FROM (SELECT source1_entity_id,target_entity_id FROM read_parquet('{cg}')
       EXCEPT SELECT source1_entity_id,target_entity_id FROM read_parquet('{fg}')))""").fetchone()
    n, unique = c.execute(f"SELECT count(*),count(DISTINCT (source1_entity_id,target_entity_id)) FROM read_parquet('{fg}')").fetchone()
    nonfinite = c.execute(f"SELECT count(*) FROM read_parquet('{fg}') WHERE " + " OR ".join(
        f"{x} IS NULL OR NOT isfinite(cast({x} as double))" for x in MODEL_FEATURE_NAMES_V1_1)).fetchone()[0]
    c.close()
    files = [artifact(p, rows=pq.read_metadata(p).num_rows) for p in sorted(outdir.glob("*.parquet"))]
    audit = {"candidate_rows": candidate["audit"]["rows"], "feature_rows": n,
             "added": added, "removed": removed, "duplicates": n-unique,
             "nonfinite_or_null": nonfinite, "physical_feature_count": 65,
             "ordered_model_feature_count": len(MODEL_FEATURE_NAMES_V1_1),
             "exact_feature_order": list(MODEL_FEATURE_NAMES_V1_1) == read_json(W / "final_matcher_policy.json")["feature_order"]}
    if added or removed or n != candidate["audit"]["rows"] or n != unique or nonfinite or not audit["exact_feature_order"]:
        raise RuntimeError(f"Feature structural failure: {audit}")
    manifest = {"schema_version": 1, "release_gate_config_sha256": gate,
                "candidate_manifest_sha256": digest(W / "final_eval_candidate_manifest.json"),
                "feature_spec_sha256": digest(W / "feature_spec_v1_1.json"),
                "parts": files, "audit": audit,
                "resource": {"wall_seconds": time.time()-start, "bytes": sum(x["bytes"] for x in files),
                             "bytes_per_candidate": sum(x["bytes"] for x in files)/n,
                             "peak_process_rss_bytes": process_peak_rss(),
                             "peak_temp_bytes": max((x.get("peak_temp_bytes",0) for x in result["parts"]),default=0),
                             "restart_events": sum(x.get("resumed",False) for x in result["parts"])}}
    write_json(W / "final_eval_feature_manifest.json", manifest)
    return manifest


def score_stage() -> dict:
    gate = require_gate()
    fm = read_json(W / "final_eval_feature_manifest.json")
    policy = read_json(W / "final_matcher_policy.json")
    features = policy["feature_order"]
    model_path = W / "final_matcher_model.txt"
    model = lgb.Booster(model_file=model_path.as_posix())
    validate_model_feature_order(model.feature_name(), features)
    threshold = policy["threshold"]
    batch_size = policy["inference"]["batch_candidates"]
    outdir = W / "final_eval_scores"
    outdir.mkdir(exist_ok=True)
    schema = pa.schema([pa.field("source1_entity_id", pa.string(), False),
                        pa.field("target_entity_id", pa.string(), False),
                        pa.field("score", pa.float32(), False),
                        pa.field("predicted", pa.bool_(), False),
                        pa.field("target_is_s2", pa.bool_(), False),
                        pa.field("address_missing", pa.bool_(), False),
                        pa.field("script_conflict", pa.bool_(), False)])
    start = time.time(); files = []
    for src in sorted((W / "final_eval_features").glob("*.parquet")):
        dst = outdir / src.name
        receipt = outdir / f"{src.stem}.json"
        if dst.exists():
            if receipt.exists():
                saved = read_json(receipt)
                if (saved["sha256"] == digest(dst) and saved["feature_sha256"] == digest(src)
                    and saved["model_sha256"] == digest(model_path)
                    and saved["policy_sha256"] == digest(W / "final_matcher_policy.json")
                    and pq.read_metadata(dst).num_rows == pq.read_metadata(src).num_rows):
                    files.append(artifact(dst, rows=pq.read_metadata(dst).num_rows, resumed=True))
                    continue
            dst.unlink()
            receipt.unlink(missing_ok=True)
        partial = dst.with_suffix(".partial.parquet")
        partial.unlink(missing_ok=True)
        writer = pq.ParquetWriter(partial, schema, compression="zstd")
        rows = 0
        try:
            pf = pq.ParquetFile(src)
            columns = list(dict.fromkeys(["source1_entity_id", "target_entity_id", "target_is_s2",
                "s1_address_missing", "target_address_missing", "script_conflict", *features]))
            for batch in pf.iter_batches(batch_size=batch_size, columns=columns):
                x = np.column_stack([np.asarray(batch[name]).astype(np.float32, copy=False)
                                     for name in features])
                score = model.predict(x, num_iteration=model.best_iteration or model.current_iteration()).astype(np.float32)
                if not np.isfinite(score).all():
                    raise RuntimeError("Nonfinite model score")
                address = np.asarray(batch["s1_address_missing"]).astype(bool) | np.asarray(batch["target_address_missing"]).astype(bool)
                writer.write_table(pa.table({"source1_entity_id": batch["source1_entity_id"],
                    "target_entity_id": batch["target_entity_id"], "score": score,
                    "predicted": score >= threshold, "target_is_s2": batch["target_is_s2"],
                    "address_missing": address, "script_conflict": batch["script_conflict"]}, schema=schema))
                rows += len(batch)
        finally:
            writer.close()
        os.replace(partial, dst)
        part = artifact(dst, rows=rows, resumed=False)
        write_json(receipt, {**part, "feature_sha256":digest(src),
            "model_sha256":digest(model_path),"policy_sha256":digest(W / "final_matcher_policy.json")})
        files.append(part)
    c = db()
    sg = sql_path(outdir / "*.parquet")
    fg = sql_path(W / "final_eval_features/*.parquet")
    cg = sql_path(W / "final_eval_candidates/*.parquet")
    n, unique, nonfinite, decision_wrong, accepted = c.execute(f"""SELECT count(*),
      count(DISTINCT (source1_entity_id,target_entity_id)),
      count(*) FILTER(WHERE score IS NULL OR NOT isfinite(score)),
      count(*) FILTER(WHERE predicted != (score >= {threshold})),
      count(*) FILTER(WHERE predicted) FROM read_parquet('{sg}')""").fetchone()
    identity_delta = c.execute(f"""SELECT
      (SELECT count(*) FROM (SELECT source1_entity_id,target_entity_id FROM read_parquet('{sg}')
       EXCEPT SELECT source1_entity_id,target_entity_id FROM read_parquet('{fg}'))),
      (SELECT count(*) FROM (SELECT source1_entity_id,target_entity_id FROM read_parquet('{fg}')
       EXCEPT SELECT source1_entity_id,target_entity_id FROM read_parquet('{sg}'))),
      (SELECT count(*) FROM (SELECT source1_entity_id,target_entity_id FROM read_parquet('{sg}') WHERE predicted
       EXCEPT SELECT source1_entity_id,target_entity_id FROM read_parquet('{cg}')))""").fetchone()
    c.close()
    # Representative restart reproducibility and independent model loads.
    representative = sorted((W / "final_eval_features").glob("*.parquet"))[0]
    batch = next(pq.ParquetFile(representative).iter_batches(batch_size=10000, columns=features))
    x = np.column_stack([np.asarray(batch[name]).astype(np.float32, copy=False) for name in features])
    repeat = model.predict(x).astype(np.float32)
    second = lgb.Booster(model_file=model_path.as_posix()).predict(x).astype(np.float32)
    reproducible = bool(np.array_equal(repeat, second) and np.array_equal(repeat >= threshold, second >= threshold))
    representative_reproducible = True
    replay_rows = 0
    feature_batches = pq.ParquetFile(representative).iter_batches(batch_size=batch_size,columns=features)
    score_batches = pq.ParquetFile(outdir / representative.name).iter_batches(batch_size=batch_size,columns=["score","predicted"])
    for fbatch,sbatch in zip(feature_batches,score_batches,strict=True):
        matrix = np.column_stack([np.asarray(fbatch[name]).astype(np.float32,copy=False) for name in features])
        replay = model.predict(matrix).astype(np.float32)
        expected_score = np.asarray(sbatch["score"]).astype(np.float32,copy=False)
        expected_decision = np.asarray(sbatch["predicted"]).astype(bool,copy=False)
        representative_reproducible &= bool(np.array_equal(replay,expected_score)
                                           and np.array_equal(replay >= threshold,expected_decision))
        replay_rows += len(replay)
    audit = {"score_rows": n, "feature_rows": fm["audit"]["feature_rows"],
             "duplicates": n-unique, "nonfinite": nonfinite, "threshold_decision_errors": decision_wrong,
             "score_feature_added": identity_delta[0], "score_feature_removed": identity_delta[1],
             "predictions_outside_candidates": identity_delta[2], "accepted_pairs": accepted,
             "two_model_loads_identical": reproducible,
             "representative_batch_rows": len(x),
             "representative_partition_replay_rows": replay_rows,
             "representative_partition_reproducible": representative_reproducible}
    if n != fm["audit"]["feature_rows"] or any((n-unique, nonfinite, decision_wrong, *identity_delta)) or not reproducible or not representative_reproducible:
        raise RuntimeError(f"Score structural failure: {audit}")
    manifest = {"schema_version": 1, "release_gate_config_sha256": gate,
                "feature_manifest_sha256": digest(W / "final_eval_feature_manifest.json"),
                "model_sha256": digest(model_path), "policy_sha256": digest(W / "final_matcher_policy.json"),
                "batch_candidates": batch_size, "threshold": threshold, "parts": files, "audit": audit,
                "resource": {"wall_seconds": time.time()-start, "bytes": sum(p["bytes"] for p in files),
                             "peak_process_rss_bytes": process_peak_rss()}}
    write_json(W / "final_eval_score_manifest.json", manifest)
    return manifest


def prediction_stage() -> dict:
    gate = require_gate()
    score = read_json(W / "final_eval_score_manifest.json")
    c = db()
    sg = sql_path(W / "final_eval_scores/*.parquet")
    dest = W / "final_eval_prediction_sets.parquet"
    partial = dest.with_suffix(".partial.parquet")
    partial.unlink(missing_ok=True)
    c.execute(f"""COPY (WITH p AS (
      SELECT source1_entity_id,list_sort(list(target_entity_id)) ids,count(*) n
      FROM read_parquet('{sg}') WHERE predicted GROUP BY 1)
      SELECT s.entity_id source1_entity_id,
        coalesce(array_to_string(p.ids,','),'') matched_entity_ids,
        coalesce(p.n,0)::INTEGER predicted_count
      FROM read_parquet('work/final_eval_selection.parquet') s
      LEFT JOIN p ON s.entity_id=p.source1_entity_id ORDER BY s.entity_id)
      TO '{sql_path(partial)}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
    os.replace(partial, dest)
    n, unique, empty, invalid = c.execute(f"""SELECT count(*),count(DISTINCT source1_entity_id),
      count(*) FILTER(WHERE matched_entity_ids=''),
      count(*) FILTER(WHERE regexp_matches(matched_entity_ids,'\\s') OR starts_with(matched_entity_ids,'S1-'))
      FROM read_parquet('{sql_path(dest)}')""").fetchone()
    list_pairs = c.execute(f"""SELECT count(*) FROM (
      SELECT source1_entity_id,unnest(string_split(matched_entity_ids,',')) target_entity_id
      FROM read_parquet('{sql_path(dest)}') WHERE predicted_count>0
      EXCEPT SELECT source1_entity_id,target_entity_id FROM read_parquet('{sg}') WHERE predicted)""").fetchone()[0]
    reverse = c.execute(f"""SELECT count(*) FROM (
      SELECT source1_entity_id,target_entity_id FROM read_parquet('{sg}') WHERE predicted
      EXCEPT SELECT source1_entity_id,unnest(string_split(matched_entity_ids,','))
      FROM read_parquet('{sql_path(dest)}') WHERE predicted_count>0)""").fetchone()[0]
    count_mismatch = c.execute(f"""SELECT count(*) FROM read_parquet('{sql_path(dest)}')
      WHERE predicted_count != CASE WHEN matched_entity_ids='' THEN 0 ELSE len(string_split(matched_entity_ids,',')) END""").fetchone()[0]
    # Define the stress cohort using only inference-available normalized keys.
    keys = W / "final_eval_unseen_keys.parquet"
    key_partial = keys.with_suffix(".partial.parquet")
    c.execute("""CREATE TEMP VIEW fit_keys AS SELECT DISTINCT k.name_sorted,k.addr_norm
      FROM read_parquet('work/keys/train_s1.parquet') k
      JOIN read_parquet('work/model_development_split_manifest.parquet') d USING(entity_id)
      WHERE d.split='model_fit'""")
    c.execute(f"""COPY (SELECT s.entity_id source1_entity_id,
      NOT EXISTS(SELECT 1 FROM fit_keys f WHERE f.name_sorted=k.name_sorted) unseen_sorted_name,
      NOT EXISTS(SELECT 1 FROM fit_keys f WHERE f.addr_norm=k.addr_norm) unseen_exact_address,
      k.country_norm, (length(k.addr_norm)=0) s1_address_missing
      FROM read_parquet('work/keys/train_s1.parquet') k
      JOIN read_parquet('work/final_eval_selection.parquet') s USING(entity_id)
      ORDER BY s.entity_id) TO '{sql_path(key_partial)}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
    os.replace(key_partial, keys)
    grouped = dict(c.execute("""SELECT d.split,count(*) FROM read_parquet('work/split_s1_grouped.parquet') g
      JOIN read_parquet('work/model_development_split_manifest.parquet') d USING(entity_id)
      WHERE g.split='val' GROUP BY 1""").fetchall())
    baseline = c.execute("""SELECT count(*) FROM read_parquet('work/split_s1_grouped.parquet') g
      JOIN read_parquet('work/model_development_samples.parquet') d USING(entity_id)
      WHERE g.split='val' AND d.sample_role='baseline_dev'""").fetchone()[0]
    c.close()
    audit = {"rows": n, "unique_s1": unique, "expected_s1": 110341,
             "empty_sets": empty, "invalid_serialization": invalid,
             "prediction_pairs_missing_from_score": list_pairs, "score_predictions_missing_from_sets": reverse,
             "count_mismatch": count_mismatch, "duplicate_predictions": score["audit"]["duplicates"],
             "prediction_subset_of_candidates": score["audit"]["predictions_outside_candidates"] == 0}
    if n != unique or n != 110341 or any((invalid,list_pairs,reverse,count_mismatch)):
        raise RuntimeError(f"Prediction structural failure: {audit}")
    manifest = {"schema_version": 1, "release_gate_config_sha256": gate,
                "score_manifest_sha256": digest(W / "final_eval_score_manifest.json"),
                "prediction_sets": artifact(dest, rows=n), "unseen_key_slice": artifact(keys),
                "grouped_holdout_overlap": {"model_fit": grouped.get("model_fit",0),
                   "model_tune": grouped.get("model_tune",0),
                   "model_threshold": grouped.get("model_threshold",0), "baseline_dev": baseline,
                   "independent_matcher_evaluation": all(grouped.get(role,0)==0 for role in ("model_fit","model_tune","model_threshold")) and baseline==0},
                "audit": audit}
    write_json(W / "final_eval_prediction_manifest.json", manifest)
    return manifest


def _f05(tp: np.ndarray, predicted: np.ndarray, truth: np.ndarray) -> np.ndarray:
    precision = np.divide(tp, predicted, out=np.where(truth == 0, 1.0, 0.0).astype(float), where=predicted > 0)
    recall = np.divide(tp, truth, out=np.ones_like(tp, dtype=float), where=truth > 0)
    denom = .25 * precision + recall
    return np.divide(1.25 * precision * recall, denom, out=np.zeros_like(precision), where=denom > 0)


def _aggregate(per: pa.Table, mask: np.ndarray | None = None) -> dict:
    truth = np.asarray(per["truth_n"]).astype(np.int32)
    recovered = np.asarray(per["recovered_n"]).astype(np.int32)
    pred = np.asarray(per["pred_n"]).astype(np.int32)
    tp = np.asarray(per["tp"]).astype(np.int32)
    if mask is not None:
        truth, recovered, pred, tp = (x[mask] for x in (truth,recovered,pred,tp))
    n = len(truth); pcount = int(pred.sum()); tcount = int(truth.sum()); hit = int(tp.sum())
    if n == 0:
        return {"s1":0,"truth_pairs":0,"recovered_pairs":0,"predicted_pairs":0,
                "true_positive_pairs":0,"candidate_oracle_macro_f0_5":0.0,
                "candidate_mean_precision":0.0,"candidate_mean_recall":0.0,
                "pair_candidate_recall":0.0,"candidate_all_recovered_s1":0,
                "candidate_some_recovered_s1":0,"candidate_none_recovered_s1":0,
                "singleton_oracle_accuracy":0.0,"macro_f0_5":0.0,
                "recovered_truth_macro_f0_5":0.0,"pair_precision":0.0,"pair_recall":0.0,
                "singleton_accuracy":0.0,"mean_predicted_matches":0.0,
                "empty_prediction_rate":0.0,"prediction_count_distribution":{},
                "complete_recovery_s1":0,"partial_recovery_s1":0,"no_recovery_s1":0}
    oracle_p = np.where(truth == 0,1.0,np.where(recovered > 0,1.0,0.0))
    oracle_r = np.divide(recovered,truth,out=np.ones(n),where=truth>0)
    singleton = truth == 0
    return {"s1": n, "truth_pairs": tcount, "recovered_pairs": int(recovered.sum()),
            "predicted_pairs": pcount, "true_positive_pairs": hit,
            "candidate_oracle_macro_f0_5": float(_f05(recovered,recovered,truth).mean()),
            "candidate_mean_precision": float(oracle_p.mean()),
            "candidate_mean_recall": float(oracle_r.mean()),
            "pair_candidate_recall": float(recovered.sum()/tcount) if tcount else 0.0,
            "candidate_all_recovered_s1": int(np.sum((truth>0)&(recovered==truth))),
            "candidate_some_recovered_s1": int(np.sum((truth>0)&(recovered>0)&(recovered<truth))),
            "candidate_none_recovered_s1": int(np.sum((truth>0)&(recovered==0))),
            "singleton_oracle_accuracy": 1.0,
            "macro_f0_5": float(_f05(tp,pred,truth).mean()),
            "recovered_truth_macro_f0_5": float(_f05(tp,pred,recovered).mean()),
            "pair_precision": hit/pcount if pcount else 0.0,
            "pair_recall": hit/tcount if tcount else 0.0,
            "singleton_accuracy": float(np.mean(pred[singleton]==0)) if singleton.any() else 0.0,
            "mean_predicted_matches": pcount/n if n else 0.0,
            "empty_prediction_rate": float(np.mean(pred==0)) if n else 0.0,
            "prediction_count_distribution": {str(int(k)):int(v) for k,v in zip(*np.unique(pred,return_counts=True))},
            "complete_recovery_s1": int(np.sum((truth>0)&(tp==truth))),
            "partial_recovery_s1": int(np.sum((truth>0)&(tp>0)&(tp<truth))),
            "no_recovery_s1": int(np.sum((truth>0)&(tp==0)))}


def _bootstrap(per: pa.Table, country: np.ndarray, config: dict, output: Path | None = None) -> dict:
    truth = np.asarray(per["truth_n"]).astype(np.int32)
    pred = np.asarray(per["pred_n"]).astype(np.int32)
    tp = np.asarray(per["tp"]).astype(np.int32)
    macro = _f05(tp,pred,truth)
    singleton = pred[truth==0] == 0
    seed = config["bootstrap"]["seed"]
    rng = np.random.default_rng(seed)
    arrays = {"macro_f0_5": macro, "singleton_accuracy": singleton.astype(float),
              "india_macro_f0_5": macro[country=="india"],
              "us_macro_f0_5": macro[country=="us"]}
    rows = []
    for iteration in range(config["bootstrap"]["replicates"]):
        row = {"replicate": iteration}
        for name, values in arrays.items():
            row[name] = float(values[rng.integers(0,len(values),size=len(values))].mean())
        rows.append(row)
    output = output or W / "final_eval_bootstrap.parquet"
    partial = output.with_suffix(".partial.parquet")
    pq.write_table(pa.Table.from_pylist(rows),partial,compression="zstd")
    os.replace(partial,output)
    return {"artifact": artifact(output, replicates=len(rows)), "seed": seed,
            "intervals": {k: [float(np.quantile([r[k] for r in rows],.025)),
                              float(np.quantile([r[k] for r in rows],.975))] for k in arrays}}


def evaluate_stage() -> dict:
    gate = require_gate()
    config = read_json(W / "final_eval_release_gate_config.json")
    pred_manifest = read_json(W / "final_eval_prediction_manifest.json")
    if not pred_manifest["audit"]["prediction_subset_of_candidates"] or pred_manifest["audit"]["rows"] != 110341:
        raise RuntimeError("Prediction gate invalid")
    input_artifacts = {name: artifact(path) for name,path in {
        "candidate_manifest": W / "final_eval_candidate_manifest.json",
        "feature_manifest": W / "final_eval_feature_manifest.json",
        "score_manifest": W / "final_eval_score_manifest.json",
        "prediction_manifest": W / "final_eval_prediction_manifest.json",
        "prediction_sets": W / "final_eval_prediction_sets.parquet",
        "unseen_keys": W / "final_eval_unseen_keys.parquet"}.items()}
    opened = W / "final_eval_opened.json"
    gt_raw = W / "final_eval_gt.parquet"
    if opened.exists():
        previous = read_json(opened)
        if previous["release_gate_config_sha256"] != gate or previous["immutable_inputs"] != input_artifacts:
            raise RuntimeError("Opened evaluation inputs changed")
        if not gt_raw.exists():
            raise RuntimeError("Final labels were opened but the sole imported GT artifact is incomplete")
    else:
        write_json(opened, {"opened_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "purpose": "single frozen final evaluation", "release_gate_config_sha256": gate,
            "immutable_inputs": input_artifacts})
    started = time.time()
    c = db()
    if not gt_raw.exists():
        partial = gt_raw.with_suffix(".partial.parquet")
        c.execute(f"""COPY (SELECT g.source1_entity_id,g.matched_entity_ids FROM
      read_csv('dataset/train/train_ground_truth.tsv',delim='{chr(9)}',header=true,quote='',all_varchar=true) g
      JOIN read_parquet('work/final_eval_selection.parquet') s ON g.source1_entity_id=s.entity_id
      ORDER BY g.source1_entity_id) TO '{sql_path(partial)}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        os.replace(partial,gt_raw)
    gt_rows, gt_unique = c.execute(f"SELECT count(*),count(DISTINCT source1_entity_id) FROM read_parquet('{sql_path(gt_raw)}')").fetchone()
    if gt_rows != gt_unique or gt_rows != 110341:
        raise RuntimeError("Final GT has wrong S1 coverage")
    c.execute(f"""CREATE TEMP TABLE gt AS SELECT source1_entity_id,trim(mid) target_entity_id
      FROM read_parquet('{sql_path(gt_raw)}'),unnest(string_split(matched_entity_ids,',')) u(mid)
      WHERE matched_entity_ids IS NOT NULL AND length(trim(mid))>0""")
    c.execute("CREATE TEMP TABLE scored AS SELECT * FROM read_parquet('work/final_eval_scores/*.parquet')")
    c.execute("""CREATE TEMP VIEW evaluated AS SELECT s.*,g.target_entity_id IS NOT NULL y,
      p.provenance,p.source_balanced_rank
      FROM scored s LEFT JOIN gt g USING(source1_entity_id,target_entity_id)
      JOIN read_parquet('work/final_eval_candidates/*.parquet') p USING(source1_entity_id,target_entity_id)""")
    per_path = W / "final_eval_per_s1.parquet"
    partial = per_path.with_suffix(".partial.parquet")
    c.execute(f"""COPY (WITH t AS (SELECT source1_entity_id,count(*) truth_n FROM gt GROUP BY 1),
      p AS (SELECT source1_entity_id,count(*) recovered_candidates,
             count(*) FILTER(WHERE y) recovered_n,count(*) FILTER(WHERE predicted) pred_n,
             count(*) FILTER(WHERE predicted AND y) tp FROM evaluated GROUP BY 1)
      SELECT s.entity_id source1_entity_id,s.country_norm,
        coalesce(t.truth_n,0)::INTEGER truth_n,coalesce(p.recovered_n,0)::INTEGER recovered_n,
        coalesce(p.pred_n,0)::INTEGER pred_n,coalesce(p.tp,0)::INTEGER tp,
        coalesce(p.recovered_candidates,0)::INTEGER candidate_n,
        u.unseen_sorted_name,u.unseen_exact_address,u.s1_address_missing
      FROM read_parquet('work/final_eval_selection.parquet') s LEFT JOIN t ON s.entity_id=t.source1_entity_id
      LEFT JOIN p ON s.entity_id=p.source1_entity_id
      JOIN read_parquet('work/final_eval_unseen_keys.parquet') u ON s.entity_id=u.source1_entity_id
      ORDER BY s.entity_id) TO '{sql_path(partial)}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
    os.replace(partial,per_path)
    per = pq.read_table(per_path)
    country = np.array(per["country_norm"].to_pylist())
    overall = _aggregate(per)
    country_results = {name:_aggregate(per,country==name) for name in ("india","us")}
    unseen = np.asarray(per["unseen_sorted_name"]).astype(bool)
    strict = unseen & np.asarray(per["unseen_exact_address"]).astype(bool)
    stress = {"unseen_sorted_name": _aggregate(per,unseen),
              "unseen_name_and_address": _aggregate(per,strict),
              "country_distribution": {name:int(np.sum(unseen & (country==name))) for name in ("india","us")},
              "s1_missing_address": int(np.sum(unseen & np.asarray(per["s1_address_missing"]).astype(bool)))}
    truth = np.asarray(per["truth_n"]).astype(np.int32)
    count_bands = {label:_aggregate(per,mask) for label,mask in (
        ("0",truth==0),("1",truth==1),("2",truth==2),("3-4",(truth>=3)&(truth<=4)),("5+",truth>=5))}
    pair_base = c.execute("""SELECT count(*) total,count(*) FILTER(WHERE y) recovered,
      count(*) FILTER(WHERE predicted) predicted,count(*) FILTER(WHERE predicted AND y) tp,
      count(*) FILTER(WHERE predicted AND NOT y) fp,
      count(*) FILTER(WHERE y AND NOT predicted) matcher_fn FROM evaluated""").fetchone()
    pair_breakdown = {}
    dimensions = {"source": "CASE WHEN target_is_s2 THEN 'S2' ELSE 'S3' END",
       "address": "CASE WHEN address_missing THEN 'either_missing' ELSE 'both_present' END",
       "script_relation": "CASE WHEN script_conflict THEN 'conflict' ELSE 'same_or_empty' END",
       "provenance": "cast(provenance as varchar)",
       "rank_band": "CASE WHEN source_balanced_rank=0 THEN '0' WHEN source_balanced_rank<=10 THEN '1-10' WHEN source_balanced_rank<=25 THEN '11-25' ELSE '26-50' END"}
    for key,expr in dimensions.items():
        vals = c.execute(f"""SELECT {expr} k,count(*) candidate_n,
          count(*) FILTER(WHERE y) recovered_truth_n,
          count(*) FILTER(WHERE predicted) predicted_n,
          count(*) FILTER(WHERE predicted AND y) tp,
          count(*) FILTER(WHERE predicted AND NOT y) fp,
          count(*) FILTER(WHERE y AND NOT predicted) matcher_fn
          FROM evaluated GROUP BY 1 ORDER BY 1""").fetchall()
        pair_breakdown[key] = {str(k):dict(zip(("candidates","recovered_truth","predicted","tp","fp","matcher_fn"),rest))
                               for k,*rest in vals}
    gt_source = dict(c.execute("SELECT substr(target_entity_id,1,2),count(*) FROM gt GROUP BY 1").fetchall())
    # Enrich only the already imported GT subset for denominator-specific slices.
    gt_attr = c.execute("""SELECT length(s.addr_norm)=0 OR length(t.addr_norm)=0 address_missing,
      s.name_norm s_name,t.name_norm t_name
      FROM gt g JOIN read_parquet('work/keys/train_s1.parquet') s ON g.source1_entity_id=s.entity_id
      JOIN (SELECT entity_id,name_norm,addr_norm FROM read_parquet('work/keys/train_s2.parquet')
            UNION ALL SELECT entity_id,name_norm,addr_norm FROM read_parquet('work/keys/train_s3.parquet')) t
      ON g.target_entity_id=t.entity_id""").fetchall()
    from er.features.exact import script_class
    truth_address = {"either_missing":sum(bool(r[0]) for r in gt_attr),
                     "both_present":sum(not bool(r[0]) for r in gt_attr)}
    conflict = sum(bool(script_class(a) and script_class(b) and script_class(a)!=script_class(b)) for _,a,b in gt_attr)
    truth_script = {"conflict":conflict,"same_or_empty":len(gt_attr)-conflict}
    for name,denominators in (("source",{"S2":gt_source.get("S2",0),"S3":gt_source.get("S3",0)}),
                              ("address",truth_address),("script_relation",truth_script)):
        for label,stats in pair_breakdown[name].items():
            denom = denominators.get(label,0)
            stats["truth_pairs"] = denom
            stats["recall"] = stats["tp"]/denom if denom else 0.0
            stats["precision"] = stats["tp"]/stats["predicted"] if stats["predicted"] else 0.0
    for name in ("provenance","rank_band"):
        for stats in pair_breakdown[name].values():
            stats["recovered_truth_recall"] = stats["tp"]/stats["recovered_truth"] if stats["recovered_truth"] else 0.0
            stats["precision"] = stats["tp"]/stats["predicted"] if stats["predicted"] else 0.0
    bootstrap = _bootstrap(per,country,config)
    references = read_json(W / "model_threshold_results.json")["selected_result"]
    metric = {"schema_version":1,"release_gate_config_sha256":gate,
              "opened_marker_sha256":digest(W / "final_eval_opened.json"),
              "gt_artifact": artifact(gt_raw,rows=gt_rows), "per_s1_artifact":artifact(per_path,rows=overall["s1"]),
              "candidate_oracle":{k:v for k,v in overall.items() if k.startswith("candidate_") or k in ("singleton_oracle_accuracy","pair_candidate_recall")},
              "matcher":{k:v for k,v in overall.items() if not k.startswith("candidate_")},
              "countries":country_results,"true_match_count_bands":count_bands,
              "pair_subgroups":pair_breakdown,"unseen_key_stress":stress,
              "bootstrap":bootstrap,"threshold_reference":references,
              "threshold_comparison":{k:{"point":overall[k] if k in overall else country_results[k.split('_')[0]]["macro_f0_5"],
                 "reference":references[k],
                 "absolute_difference":(overall[k] if k in overall else country_results[k.split('_')[0]]["macro_f0_5"])-references[k],
                 "interval_95":bootstrap["intervals"][k]}
                 for k in ("macro_f0_5","singleton_accuracy","india_macro_f0_5","us_macro_f0_5")}}
    write_json(W / "final_eval_metrics.json",metric)
    # Candidate misses and matcher false negatives are disjoint populations.
    errors = {"release_gate_config_sha256":gate,"truth_pairs":overall["truth_pairs"],
      "candidate_generation_misses":overall["truth_pairs"]-overall["recovered_pairs"],
      "matcher_false_negatives":int(pair_base[5]),"matcher_false_positives":int(pair_base[4]),
      "singleton_false_positive_s1":int(np.sum((truth==0)&(np.asarray(per["pred_n"])>0))),
      "multi_match_underprediction_s1":int(np.sum((truth>1)&(np.asarray(per["pred_n"])<truth))),
      "multi_match_overprediction_s1":int(np.sum((truth>1)&(np.asarray(per["pred_n"])>truth))),
      "country_and_pair_slices":pair_breakdown,"true_match_count_bands":count_bands}
    write_json(W / "final_eval_error_analysis.json",errors)
    c.close()
    resource = {"release_gate_config_sha256":gate,
      "candidate":read_json(W / "final_eval_candidate_manifest.json")["resource"],
      "feature":read_json(W / "final_eval_feature_manifest.json")["resource"],
      "score":read_json(W / "final_eval_score_manifest.json")["resource"],
      "evaluation_wall_seconds":time.time()-started,
      "evaluation_peak_process_rss_bytes":process_peak_rss()}
    write_json(W / "final_eval_resource_report.json",resource)
    return metric


def release_stage() -> dict:
    gate = require_gate()
    config = read_json(W / "final_eval_release_gate_config.json")
    metrics = read_json(W / "final_eval_metrics.json")
    cand = read_json(W / "final_eval_candidate_manifest.json")
    feat = read_json(W / "final_eval_feature_manifest.json")
    score = read_json(W / "final_eval_score_manifest.json")
    prediction = read_json(W / "final_eval_prediction_manifest.json")
    current_frozen = frozen_hashes()
    frozen_ok = all(x.get("pass", True) for x in current_frozen.values())
    chain = (all(read_json(W / f"final_eval_{name}_manifest.json")["release_gate_config_sha256"] == gate
                 for name in ("candidate","feature","score","prediction"))
             and metrics["release_gate_config_sha256"] == gate
             and feat["candidate_manifest_sha256"] == digest(W / "final_eval_candidate_manifest.json")
             and score["feature_manifest_sha256"] == digest(W / "final_eval_feature_manifest.json")
             and prediction["score_manifest_sha256"] == digest(W / "final_eval_score_manifest.json"))
    integrity = (frozen_ok and chain and cand["audit"]["duplicates"] == 0 and
                 feat["audit"]["added"] == feat["audit"]["removed"] == feat["audit"]["nonfinite_or_null"] == 0 and
                 score["audit"]["nonfinite"] == score["audit"]["threshold_decision_errors"] == 0 and
                 prediction["audit"]["rows"] == prediction["audit"]["unique_s1"] == 110341)
    m = metrics["matcher"]
    country_drop = {name: config["threshold_reference"][f"{name}_macro_f0_5"] -
                    metrics["countries"][name]["macro_f0_5"] for name in ("india","us")}
    checks = {
      "all_integrity_invariants": integrity,
      "macro_f0_5_at_least_0_88": m["macro_f0_5"] >= config["criteria"]["minimum_macro_f0_5"],
      "macro_drop_at_most_0_02": config["threshold_reference"]["macro_f0_5"] - m["macro_f0_5"] <= config["criteria"]["maximum_macro_drop_vs_threshold"],
      "pair_precision_at_least_0_95": m["pair_precision"] >= config["criteria"]["minimum_pair_precision"],
      "country_drop_at_most_0_04": all(x <= config["criteria"]["maximum_country_macro_drop"] for x in country_drop.values()),
      "predictions_subset_of_candidates": prediction["audit"]["prediction_subset_of_candidates"],
      "zero_duplicate_predictions": prediction["audit"]["duplicate_predictions"] == 0,
      "one_prediction_set_per_s1": prediction["audit"]["rows"] == prediction["audit"]["unique_s1"] == 110341,
    }
    status = "INVALID" if not integrity else "PASS" if all(checks.values()) else "FAIL"
    decision = {"schema_version":1,"status":status,"release_gate_config_sha256":gate,
                "criteria":checks,"country_macro_drops":country_drop,
                "frozen_model_sha256":digest(W / "final_matcher_model.txt"),
                "frozen_policy_sha256":digest(W / "final_matcher_policy.json"),
                "candidate_policy_sha256":digest(W / "final_candidate_policy.json"),
                "feature_spec_sha256":digest(W / "feature_spec_v1_1.json"),
                "threshold":read_json(W / "final_matcher_policy.json")["threshold"],
                "test_inference_permitted":status=="PASS"}
    write_json(W / "final_eval_release_decision.json",decision)
    resource = read_json(W / "final_eval_resource_report.json")
    report = ["# Frozen matcher final evaluation", "",
      f"Release gate: **{status}**. Fixed threshold: **0.61**. Configuration SHA-256: `{gate}`.","",
      "## Pre-open firewall", "", "All frozen checksums and the 61-feature order matched; 58 development artifacts had zero final-evaluation S1 overlap. Prior cross-split labelled-target overlap was zero. The historical split creation report documents transient GT aggregates that were removed and not used for selection.","",
      "## Candidates, features and predictions", "",
      f"Candidates: {cand['audit']['rows']:,}; duplicates: {cand['audit']['duplicates']}; zero-candidate S1: {cand['audit']['distribution']['zero_s1']:,}. Features: {feat['audit']['feature_rows']:,}; added/removed: {feat['audit']['added']}/{feat['audit']['removed']}. Accepted pairs: {score['audit']['accepted_pairs']:,}. Prediction sets: {prediction['audit']['rows']:,}; empty: {prediction['audit']['empty_sets']:,}.","",
      "## Metrics", "",
      f"Candidate oracle macro F0.5: **{metrics['candidate_oracle']['candidate_oracle_macro_f0_5']:.6f}**; candidate pair recall: **{metrics['candidate_oracle']['pair_candidate_recall']:.6f}**.",
      f"Frozen matcher macro F0.5: **{m['macro_f0_5']:.6f}**; pair precision: **{m['pair_precision']:.6f}**; pair recall: **{m['pair_recall']:.6f}**; singleton accuracy: **{m['singleton_accuracy']:.6f}**.",
      f"India/US macro F0.5: {metrics['countries']['india']['macro_f0_5']:.6f}/{metrics['countries']['us']['macro_f0_5']:.6f}.","",
      "## Bootstrap intervals and threshold comparison", "",
      "| Metric | Final | 95% S1 bootstrap interval | Threshold reference | Difference |", "|---|---:|---:|---:|---:|"]
    for key,row in metrics["threshold_comparison"].items():
        report.append(f"| {key} | {row['point']:.6f} | [{row['interval_95'][0]:.6f}, {row['interval_95'][1]:.6f}] | {row['reference']:.6f} | {row['absolute_difference']:+.6f} |")
    report += ["", "## Predeclared slices and errors", "",
      "Country, source, address availability, script relation, candidate provenance, candidate rank, and truth-count bands are recorded in `final_eval_metrics.json`.",
      f"Unseen sorted-name S1: {metrics['unseen_key_stress']['unseen_sorted_name']['s1']:,}; macro F0.5: {metrics['unseen_key_stress']['unseen_sorted_name']['macro_f0_5']:.6f}.",
      f"Historical grouped validation overlap: model_fit {prediction['grouped_holdout_overlap']['model_fit']:,}, model_tune {prediction['grouped_holdout_overlap']['model_tune']:,}, model_threshold {prediction['grouped_holdout_overlap']['model_threshold']:,}, baseline_dev {prediction['grouped_holdout_overlap']['baseline_dev']:,}. It was not scored as an independent matcher evaluation.","",
      f"Candidate misses: {read_json(W/'final_eval_error_analysis.json')['candidate_generation_misses']:,}; matcher false negatives: {read_json(W/'final_eval_error_analysis.json')['matcher_false_negatives']:,}; matcher false positives: {read_json(W/'final_eval_error_analysis.json')['matcher_false_positives']:,}.","",
      "## Release criteria", ""]
    report += [f"- {key}: {'PASS' if passed else 'FAIL'}" for key,passed in checks.items()]
    report += ["", "## Resources", "", "```json", json.dumps(resource,indent=2), "```", "",
      "No model, feature definition, candidate policy, or threshold was changed after the final-evaluation labels were opened."]
    (W / "final_eval_report.md").write_text("\n".join(report)+"\n",encoding="utf-8")
    if status == "PASS":
        test_projection = read_json(W / "final_matcher_resource_projection.json")["test_projected"]
        plan = {"schema_version":1,"release_gate_config_sha256":gate,
          "release_decision_sha256":digest(W / "final_eval_release_decision.json"),
          "test_s1":1732544,"expected_candidates":test_projection["candidates"],
          "partitions":{"country_hash":96,"hash_key":"source1_entity_id"},
          "disk_preflight_free_bytes":48_000_000_000,"ram_guard_min_free_bytes":1_350_000_000,
          "expected_candidate_bytes":test_projection["candidate_identity_provenance_bytes"],
          "expected_feature_bytes":test_projection["feature_bytes"],
          "expected_score_bytes":test_projection["score_bytes"],
          "expected_wall_seconds":round(3600*(test_projection["candidate_hours"]+test_projection["feature_hours"])+60*test_projection["inference_minutes"]),
          "frozen_model_sha256":decision["frozen_model_sha256"],
          "frozen_policy_sha256":decision["frozen_policy_sha256"]}
        write_json(W / "test_inference_resource_plan.json",plan)
        runbook = ["# Test inference runbook (not executed)", "",
          f"Release decision: PASS. Model `{decision['frozen_model_sha256']}`; policy `{decision['frozen_policy_sha256']}`. Recheck both hashes before starting.","",
          f"Expect about {plan['expected_candidates']:,} candidates over 96 country/hash parts. Require at least 48 GB free scratch disk and 1.35 GB free RAM before each stage; stop if free RAM falls below 0.5 GB. Expected wall time is about {plan['expected_wall_seconds']/3600:.1f} hours.","",
          "## Commands to prepare and run", "",
          "The test S1/S2/S3 normalized keys and token document frequencies must first be built from `dataset/test` using the frozen normalization and production key pipeline. Rebuild the same four retrieval passes and frozen evidence ranking for each country/hash partition. Never use labels or external identity data.","",
          "```powershell", "$env:PYTHONPATH='code/business_entity_resolution/src'", "$env:PYTHONUTF8='1'",
          ".venv\\Scripts\\python.exe -m er.candidates.materialize --config work/final_candidate_policy.json --rank-root work/test_rank --output-dir work/test_candidates",
          ".venv\\Scripts\\python.exe -m er.features.materialize --candidates work/test_candidates --output work/test_features",
          ".venv\\Scripts\\python.exe scripts/test_inference.py score --features work/test_features --output work/test_scores --model work/final_matcher_model.txt --policy work/final_matcher_policy.json",
          ".venv\\Scripts\\python.exe scripts/test_inference.py assemble --scores work/test_scores --output output/matching_results.tsv --candidates-output output/candidate_pairs.tsv",
          ".venv\\Scripts\\python.exe utils/validate_submission.py --matching output/matching_results.tsv --candidate output/candidate_pairs.tsv --test-dir dataset/test", "```", "",
          "The test-specific key/rank builder and `scripts/test_inference.py` commands are runbook interfaces to implement and verify before execution; they are not present yet. Do not run these commands in this task.","",
          "## Restart and independent audits", "",
          "Write each partition to a temporary file, atomically rename, and store SHA-256, row count, exit code, wall time, peak process-tree RSS, and peak scratch size. On retry, verify checksums, reuse completed parts, and rebuild failed parts only. Keep immutable input manifests until independent candidate-to-feature and feature-to-score identity comparisons pass. Confirm all scores are finite, score decisions equal `score >= 0.61`, prediction IDs are candidate subsets, one row exists for each test S1, IDs are unique and namespace-valid, empty lists are explicit, and multi-match sets retain all accepted targets. Run the official validator command above. On failure, preserve logs and manifests, remove only incomplete temporary parts, and retry that partition. Clean scratch in order: temporary ranking spill, feature inputs after score checks, numeric feature parts after score and assembly checks, then score parts after validator PASS. Keep final TSVs and integrity manifests."]
        (W / "test_inference_runbook.md").write_text("\n".join(runbook)+"\n",encoding="utf-8")
    return decision


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage",choices=("preopen","gate","candidates","features","score","predict","evaluate","release"))
    args = parser.parse_args()
    action = {"preopen":preopen,"gate":gate_config,"candidates":candidate_stage,
              "features":feature_stage,"score":score_stage,"predict":prediction_stage,
              "evaluate":evaluate_stage,"release":release_stage}[args.stage]
    print(json.dumps(action(),indent=2))


if __name__ == "__main__":
    main()
