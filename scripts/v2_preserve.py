"""Verify the submitted release and create a separate, read-only V1 backup.

Only bytes/checksums of authorized release outputs are read. No dataset is opened.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "broCode_submission.zip": "126f2d4652169b26c90e61978e9825be5ccc89048eccf1be10dcf6549ea4b98d",
    "output/candidate_pairs.tsv": "e720888b85f0a4742558844e7d26a871c05ddc7a7bdd9574917d4ad183df13d3",
    "output/matching_results.tsv": "82c7ba426c1984d13952d3c8f25e79c594a8c7a66a61f4fdcb9c634b090d1b22",
    "work/final_candidate_policy.json": "46fd324b3d4db7dbdf5fa4b8ce528f681335ce16f759fb93a39ba5b98a040fea",
    "work/feature_spec_v1_1.json": "37982fd6377aa2e36fe1c5dec0484a35c02ba4bcc4e3b67b3a337b33597f6e1f",
    "work/final_matcher_model.txt": "76ff78a7cc97e40174b6d631a6eaa062c786f0480c6693fd6387e90be6761d21",
    "work/final_matcher_policy.json": "ced4ee4133110f5f55c184c3d4020b5087ecac5398bdf9ebfd03f76b3662aba3",
    "work/final_eval_release_gate_config.json": "17165a4c083e73c747d5b57ca9e758959b0a8609d7306d738ad7ecb97326c6e9",
    "code/business_entity_resolution/src/er/normalize.py": "b3508b600e05e6008ceb7872d7b48e715df935ee7c4bb586d1cd7d5ec02ccedd",
}
RELEASE_COMMIT = "f6917432f267f8b36aefb4f392e2040069377dc2"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def run() -> dict:
    archive_manifest = json.loads((ROOT / "work/submission_archive_manifest.json").read_text())
    expected = dict(EXPECTED)
    def add_expected(items: dict) -> None:
        for path, digest in items.items():
            if path in expected and expected[path] != digest:
                raise RuntimeError(f"Conflicting frozen checksum expectations: {path}")
            expected[path] = digest
    add_expected({p: v["sha256"] for p, v in archive_manifest["files"].items()})
    candidate_manifest = json.loads((ROOT / "work/final_candidate_policy_manifest.json").read_text())
    add_expected({v["path"]: v["sha256"] for v in candidate_manifest["frozen_inputs"].values()
                  if "path" in v and "sha256" in v})
    add_expected(candidate_manifest["ranking_code"]["files"])
    matcher_manifest = json.loads((ROOT / "work/final_matcher_manifest.json").read_text())
    add_expected({item["path"]: item["sha256"] for item in matcher_manifest["artifacts"]})
    actual = {p: {"expected_sha256": d, "actual_sha256": sha(ROOT / p),
                  "bytes": (ROOT / p).stat().st_size} for p, d in sorted(expected.items())}
    if any(v["expected_sha256"] != v["actual_sha256"] for v in actual.values()):
        raise RuntimeError("V1 checksum mismatch; no snapshot will be created")
    tag = subprocess.check_output(["git", "rev-parse", "release_v1"], cwd=ROOT, text=True).strip()
    if tag != RELEASE_COMMIT:
        raise RuntimeError("release_v1 tag does not point to the recorded release commit")
    source_paths = ["code", "scripts", "Documentation_template.md", "CHALLENGE.md",
                    "ProblemStatement.txt", ".gitignore"]
    subprocess.run(["git", "diff", "--exit-code", "--diff-filter=CDMRTUXB", "release_v1", "--", *source_paths],
                   cwd=ROOT, check=True, stdout=subprocess.PIPE)
    backup = ROOT / "work/v2_release_v1"
    backup.mkdir(parents=True, exist_ok=True)
    dest = backup / "broCode_submission.zip"
    if not dest.exists():
        shutil.copyfile(ROOT / "broCode_submission.zip", dest)
    if sha(dest) != EXPECTED["broCode_submission.zip"]:
        raise RuntimeError("Existing V1 backup checksum differs; refusing overwrite")
    snapshot = backup / "source_snapshot.zip"
    if not snapshot.exists():
        subprocess.run(["git", "archive", "--format=zip", "-o", str(snapshot), "release_v1",
                        *source_paths], cwd=ROOT, check=True)
    # Recreate the expected bytes from the immutable commit, including on restarts.
    process = subprocess.Popen(["git", "archive", "--format=zip", "release_v1", *source_paths],
                               cwd=ROOT, stdout=subprocess.PIPE)
    reference = hashlib.sha256()
    for block in iter(lambda: process.stdout.read(8 << 20), b""):
        reference.update(block)
    process.stdout.close()
    if process.wait() != 0 or sha(snapshot) != reference.hexdigest():
        raise RuntimeError("Source snapshot differs from release_v1; refusing overwrite")
    for path in (dest, snapshot):
        path.chmod(stat.S_IREAD)
    result = {"status": "PASS", "checked_utc": datetime.now(timezone.utc).isoformat(),
              "release_commit": RELEASE_COMMIT, "release_tag": "release_v1",
              "research_branch": "codex/v2-candidate-research", "artifacts": actual,
              "protected_snapshot": {"archive": dest.relative_to(ROOT).as_posix(),
                "archive_sha256": sha(dest), "source": snapshot.relative_to(ROOT).as_posix(),
                "source_sha256": sha(snapshot), "protection": "separate read-only files; preserved Git tag"},
              "public_score_context_only": {"v1": 0.876359, "visible_first": 0.991811,
                "source": "user supplied; not an optimization metric"},
              "source_snapshot_verified_against_release_commit": True,
              "tracked_v1_source_diff_empty": True,
              "reproducibility_evidence": {"prior_archive_audit": "work/submission_archive_manifest.json",
                "audit_sha256": sha(ROOT / "work/submission_archive_manifest.json"),
                "extracted_checksums_pass": archive_manifest["archive"]["extracted_checksums_pass"],
                "import_smoke_pass": archive_manifest["archive"]["import_smoke_pass"],
                "extracted_validator_exit": archive_manifest["archive"]["extracted_validator_exit"],
                "rerun_real_test_inference": False}, "dataset_access": False}
    (ROOT / "work/v2_v1_immutability_manifest.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    lines = ["# V2: V1 preservation proof", "", "PASS: all recorded release hashes match.", "",
             f"Release commit and preserved tag: `{RELEASE_COMMIT}` / `release_v1`.",
             "Research branch: `codex/v2-candidate-research`.", "",
             "The complete submitted ZIP and a Git source snapshot are backed up as read-only files in `work/v2_release_v1/`.",
             "The source snapshot excludes the real dataset directory and work intermediates; it includes tracked synthetic test fixtures. Its bytes are verified against a fresh Git archive of release_v1. The ZIP retains exactly the submitted files.",
             "Prior clean extraction, import and validator results are referenced by checksum; no real test inference was rerun.", "",
             "User supplied leaderboard context: V1 0.876359; visible first 0.991811. Neither is a research optimization metric.", "",
             "| Artifact | SHA-256 |", "|---|---|"]
    lines += [f"| {p} | `{actual[p]['actual_sha256']}` |" for p in EXPECTED]
    (ROOT / "work/v2_v1_immutability_report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    return {"status": "PASS", "checked_artifacts": len(actual), "snapshot": result["protected_snapshot"]}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
