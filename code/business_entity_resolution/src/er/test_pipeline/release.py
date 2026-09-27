"""Sequential, restartable frozen test release driver.

Each stage has its own durable artifacts and can be resumed independently. Large
stages never overlap. This module does not read labels or alter frozen inputs.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .common import save_json, sha256

STAGES = [
    ("candidate_audit", ["-m", "er.test_pipeline.candidate_report"]),
    ("features", ["-m", "er.test_pipeline.features"]),
    ("score", ["-m", "er.test_pipeline.score"]),
    ("assemble", ["-m", "er.test_pipeline.assemble"]),
    ("output_audit", ["-m", "er.test_pipeline.audit"]),
    ("diagnostics", ["-m", "er.test_pipeline.diagnostics"]),
]


def free_ram() -> int:
    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                    ("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                    ("page_total", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong),
                    ("virtual_total", ctypes.c_ulonglong), ("virtual_free", ctypes.c_ulonglong),
                    ("extended", ctypes.c_ulonglong)]
    status = Status()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed")
    return status.free


def guard() -> dict:
    result = {"free_ram_bytes": free_ram(), "free_disk_bytes": shutil.disk_usage(Path.cwd()).free}
    if result["free_ram_bytes"] < 1_350_000_000 or result["free_disk_bytes"] < 48_000_000_000:
        raise RuntimeError(f"Release resource guard failed: {result}")
    return result


def execute_stage(name: str, argv: list[str], env: dict[str,str]) -> dict:
    before = guard()
    command = [sys.executable, *argv]
    log = Path("work") / f"test_release_{name}.log"
    started = datetime.now(timezone.utc)
    print(f"{started.isoformat()} START {name}: {' '.join(command)}", flush=True)
    with log.open("w", encoding="utf-8", newline="") as stream:
        completed = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT)
    finished = datetime.now(timezone.utc)
    result = {"stage": name, "command": command, "started_utc": started.isoformat(),
              "finished_utc": finished.isoformat(), "wall_seconds": (finished-started).total_seconds(),
              "exit_code": completed.returncode, "resource_preflight": before,
              "log": log.as_posix(), "log_sha256": sha256(log)}
    save_json(Path("work") / f"test_release_{name}.json", result)
    if completed.returncode:
        raise RuntimeError(f"Release stage {name} failed; inspect {log}")
    print(f"{finished.isoformat()} PASS {name}", flush=True)
    return result


def validators(env: dict[str,str]) -> list[dict]:
    result = []
    for check_ids in (False, True):
        name = "official_validator_id_check" if check_ids else "official_validator"
        validator_entry = (["utils/validate_submission.py"] if Path("utils/validate_submission.py").is_file()
                           else ["-m", "er.test_pipeline.official_validator"])
        argv = [*validator_entry, "--matching", "output/matching_results.tsv",
                "--candidate", "output/candidate_pairs.tsv", "--test-dir", "dataset/test"]
        if check_ids:
            argv.append("--check-ids")
        before = guard()
        started = datetime.now(timezone.utc)
        command = [sys.executable, *argv]
        completed = subprocess.run(command, env=env, capture_output=True, text=True)
        finished = datetime.now(timezone.utc)
        log = Path("work") / f"{name}.log"
        log.write_text("COMMAND: " + " ".join(command) + "\nSTDOUT:\n" + completed.stdout
                       + "\nSTDERR:\n" + completed.stderr, encoding="utf-8")
        record = {"command": command, "started_utc": started.isoformat(),
                  "finished_utc": finished.isoformat(), "exit_code": completed.returncode,
                  "stdout": completed.stdout, "stderr": completed.stderr,
                  "resource_preflight": before,
                  "matching_sha256": sha256(Path("output/matching_results.tsv")),
                  "candidate_sha256": sha256(Path("output/candidate_pairs.tsv")),
                  "validator_variant": "large-file streaming extension; original challenge helper preserved separately"}
        result.append(record)
        if completed.returncode or "PASS" not in completed.stdout or "Matches outside candidates" in completed.stdout:
            save_json(Path("work/official_validator_summary.json"), {"runs": result, "status": "FAIL"})
            raise RuntimeError(f"Official validator command failed: {name}")
    save_json(Path("work/official_validator_summary.json"), {"runs": result, "status": "PASS"})
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-from", choices=[name for name,_ in STAGES] + ["validators"],
                        default="candidate_audit")
    args = parser.parse_args()
    if json.loads(Path("work/final_eval_release_decision.json").read_text())["status"] != "PASS":
        raise RuntimeError("Frozen release gate is not PASS")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env.setdefault("ER_FEATURE_DUCKDB_MEMORY_MB", "1200")
    names = [name for name,_ in STAGES] + ["validators"]
    for name, argv in STAGES:
        if names.index(name) < names.index(args.resume_from):
            continue
        execute_stage(name, argv, env)
    validators(env)


if __name__ == "__main__":
    main()
