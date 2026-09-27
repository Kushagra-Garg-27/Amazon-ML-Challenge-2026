"""Stage, package, extract and verify the challenge release allowlist."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TEAM = "broCode"
FROZEN = {
    "final_candidate_policy.json": "46fd324b3d4db7dbdf5fa4b8ce528f681335ce16f759fb93a39ba5b98a040fea",
    "feature_spec_v1_1.json": "37982fd6377aa2e36fe1c5dec0484a35c02ba4bcc4e3b67b3a337b33597f6e1f",
    "final_matcher_model.txt": "76ff78a7cc97e40174b6d631a6eaa062c786f0480c6693fd6387e90be6761d21",
    "final_matcher_policy.json": "ced4ee4133110f5f55c184c3d4020b5087ecac5398bdf9ebfd03f76b3662aba3",
    "final_eval_release_gate_config.json": "17165a4c083e73c747d5b57ca9e758959b0a8609d7306d738ad7ecb97326c6e9",
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def allowed() -> list[Path]:
    fixed = [Path("output/matching_results.tsv"), Path("output/candidate_pairs.tsv"),
             Path("Documentation_template.md"),
             Path("code/business_entity_resolution/README.md"),
             Path("code/business_entity_resolution/requirements.txt"),
             Path("code/business_entity_resolution/constraints.txt")]
    base = Path("code/business_entity_resolution/src/er")
    production_modules = (
        "__init__.py", "io.py", "normalize.py",
        "resources/__init__.py", "resources/suffixes.py",
        "candidates/__init__.py", "candidates/pilot.py", "candidates/policies.py",
        "candidates/ranking.py", "features/__init__.py", "features/exact.py",
        "features/fuzzy.py", "features/materialize.py", "features/schema.py",
        "features/token.py", "matcher/__init__.py", "matcher/controlled.py",
    )
    src = sorted([base / name for name in production_modules]
                 + list((base / "test_pipeline").glob("*.py")))
    release = sorted(Path("code/business_entity_resolution/release").glob("*"))
    return fixed + src + release


def scan(path: Path) -> None:
    parts = [x.lower() for x in path.parts]
    denied_parts = {"dataset", ".venv", "__pycache__", "temp", "tmp", "cache", "caches", "scores", "secrets", "work"}
    if any(x in denied_parts for x in parts) or path.suffix.lower() in {".pyc", ".parquet", ".duckdb", ".env"}:
        raise RuntimeError(f"Forbidden archive path: {path}")
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"Unsafe archive path: {path}")


def stage(dest: Path) -> dict:
    if dest.exists():
        raise RuntimeError(f"Stage destination already exists: {dest}")
    files = allowed()
    for relative in files:
        scan(relative)
        source = ROOT / relative
        if not source.is_file():
            raise RuntimeError(f"Missing release file: {source}")
        if source.suffix.lower() in {".py", ".md", ".json", ".txt"} and source.name != "final_matcher_model.txt":
            content = source.read_text(encoding="utf-8")
            if re.search(r"(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", content):
                raise RuntimeError(f"Private key in release file: {source}")
            if re.search(r"(?i)(?:api[_-]?key|password|access[_-]?token)\s*=\s*['\"][^'\"]{8,}['\"]", content):
                raise RuntimeError(f"Possible credential in release file: {source}")
            if re.search(r"[A-Za-z]:[\\/](?:Users|Projects)[\\/]", content):
                raise RuntimeError(f"Absolute workstation path in release file: {source}")
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for name, digest in FROZEN.items():
        if sha(dest / "code/business_entity_resolution/release" / name) != digest:
            raise RuntimeError(f"Frozen release copy changed: {name}")
    manifest = {p.relative_to(dest).as_posix(): {"bytes": p.stat().st_size, "sha256": sha(p)}
                for p in sorted(dest.rglob("*")) if p.is_file()}
    if set(manifest) != {x.as_posix() for x in files}:
        raise RuntimeError("Stage allowlist mismatch")
    previous = json.loads((ROOT / "work/test_output_integrity.json").read_text(encoding="utf-8"))
    if previous.get("status") != "PASS":
        raise RuntimeError("Independent output audit has not passed")
    if sha(dest / "output/candidate_pairs.tsv") != previous["candidate_file"]["sha256"] or sha(dest / "output/matching_results.tsv") != previous["matching_file"]["sha256"]:
        raise RuntimeError("Staged outputs differ from independently audited outputs")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "code/business_entity_resolution/src")
    audit_command = [sys.executable, "-m", "er.test_pipeline.audit", "--output", str(dest / "output")]
    rerun = subprocess.run(audit_command, cwd=ROOT, env=env, capture_output=True, text=True)
    (ROOT / "work/staged_output_audit.log").write_text(rerun.stdout + rerun.stderr, encoding="utf-8")
    if rerun.returncode or '"status": "PASS"' not in rerun.stdout:
        raise RuntimeError("Independent staged output audit failed")
    validator = [sys.executable, str(ROOT / "utils/validate_submission.py"),
                 "--matching", str(dest / "output/matching_results.tsv"),
                 "--candidate", str(dest / "output/candidate_pairs.tsv"),
                 "--test-dir", str(ROOT / "dataset/test")]
    checked = subprocess.run(validator, cwd=ROOT, env=env, capture_output=True, text=True)
    (ROOT / "work/staged_validator.log").write_text(checked.stdout + checked.stderr, encoding="utf-8")
    if checked.returncode or "PASS" not in checked.stdout or "Matches outside candidates" in checked.stdout:
        raise RuntimeError("Staged official validator failed")
    return manifest


def archive(stage_dir: Path, destination: Path, manifest: dict) -> dict:
    if destination.exists():
        raise RuntimeError(f"Archive exists: {destination}")
    partial = destination.with_suffix(".partial.zip")
    partial.unlink(missing_ok=True)
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
                         allowZip64=True) as zf:
        for name in manifest:
            zf.write(stage_dir / name, arcname=name)
    os.replace(partial, destination)
    with zipfile.ZipFile(destination) as zf:
        names = zf.namelist()
        if names != list(manifest) or zf.testzip() is not None:
            raise RuntimeError("ZIP membership or CRC failure")
        with tempfile.TemporaryDirectory(prefix="release_extract_", dir=ROOT / "work") as temp:
            extracted = Path(temp)
            zf.extractall(extracted)
            for name, details in manifest.items():
                if sha(extracted / name) != details["sha256"]:
                    raise RuntimeError(f"Extracted checksum mismatch: {name}")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(extracted / "code/business_entity_resolution/src")
            command = [sys.executable, "-c", "import er.test_pipeline.ingest,er.test_pipeline.candidates,er.test_pipeline.features,er.test_pipeline.score,er.test_pipeline.assemble,er.test_pipeline.audit"]
            imported = subprocess.run(command, cwd=extracted, env=env, capture_output=True, text=True)
            if imported.returncode:
                raise RuntimeError(imported.stderr)
            validator = [sys.executable, str(extracted / "code/business_entity_resolution/src/er/test_pipeline/official_validator.py"),
                "--matching", str(extracted / "output/matching_results.tsv"),
                "--candidate", str(extracted / "output/candidate_pairs.tsv"),
                "--test-dir", str(ROOT / "dataset/test")]
            checked = subprocess.run(validator, cwd=extracted, env=env, capture_output=True, text=True)
            (ROOT / "work/extracted_validator.log").write_text(checked.stdout + checked.stderr, encoding="utf-8")
            if checked.returncode or "PASS" not in checked.stdout or "Matches outside candidates" in checked.stdout:
                raise RuntimeError("Extracted output validator failed")
    return {"archive": destination.as_posix(), "bytes": destination.stat().st_size,
            "sha256": sha(destination), "files": len(manifest), "extracted_checksums_pass": True,
            "import_smoke_pass": True, "extracted_validator_exit": checked.returncode}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", type=Path, default=Path("work/release_stage"))
    parser.add_argument("--archive", type=Path, default=Path(f"{TEAM}_submission.zip"))
    args = parser.parse_args()
    os.chdir(ROOT)
    manifest = stage(args.stage_dir)
    result = archive(args.stage_dir, args.archive, manifest)
    report = {"status": "PASS", "team": TEAM, "archive": result, "files": manifest}
    Path("work/submission_archive_manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    Path("work/submission_archive_audit.md").write_text(
        "# Submission archive audit\n\nPASS: clean allowlist stage, frozen checksums, ZIP CRC, extraction checksums, import smoke.\n\n"
        + "```json\n" + json.dumps(result, indent=2) + "\n```\n", encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
