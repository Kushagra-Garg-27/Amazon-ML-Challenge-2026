"""Release execution invariants on small deterministic artifacts."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "business_entity_resolution" / "src"))

from er.test_pipeline import assemble, audit
from er.test_pipeline.common import record_part, valid_part, sha256
from er.test_pipeline.score import MODEL_SHA, POLICY_SHA
from scripts.build_submission import scan
from er.test_pipeline.official_validator import validate_large_streaming
from er.io import ingest_source, _COLS


class TestReleasePipeline(unittest.TestCase):
    def test_test_ingestion_preserves_france_schema_and_id(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            raw = root / "test_source1.tsv"
            raw.write_text("entity_id\tbusiness_name\tbusiness_address\tcountry\n"
                           "S1-FR_01\tCafé & Co\t\tFrance\n",
                           encoding="utf-8", newline="\n")
            out = root / "normalized.parquet"
            self.assertEqual(ingest_source(str(raw), str(out)), 1)
            self.assertEqual(pq.read_schema(out).names, _COLS)
            row = pq.read_table(out).to_pylist()[0]
            self.assertEqual(row["entity_id"], "S1-FR_01")
            self.assertEqual(row["business_name"], "Café & Co")
            self.assertEqual(row["country_norm"], "france")

    def test_candidate_identity_audit_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "pairs.parquet"
            pq.write_table(pa.table({"source1_entity_id": ["S1-1", "S1-1"],
                                     "target_entity_id": ["S2-1", "S2-1"]}), path)
            with self.assertRaisesRegex(RuntimeError, "Duplicate"):
                audit.parquet_identity(path)

    def test_release_stage_excludes_data_and_intermediates(self):
        scan(Path("code/business_entity_resolution/src/er/test_pipeline/score.py"))
        scan(Path("Documentation_template.md"))
        for bad in ("dataset/test/test_source1.tsv", "work/test_scores/p00_france.parquet",
                    "code/business_entity_resolution/src/er/__pycache__/cache.pyc"):
            with self.assertRaises(RuntimeError):
                scan(Path(bad))

    def test_frozen_model_and_feature_order(self):
        policy = json.loads((ROOT / "work/final_matcher_policy.json").read_text())
        self.assertEqual(sha256(ROOT / "work/final_matcher_model.txt"), MODEL_SHA)
        self.assertEqual(sha256(ROOT / "work/final_matcher_policy.json"), POLICY_SHA)
        self.assertEqual(len(policy["feature_order"]), 61)
        self.assertEqual(policy["threshold"], 0.61)
        self.assertTrue(policy["set_assembly"]["allow_empty"])
        self.assertTrue(policy["set_assembly"]["allow_multiple"])

    def test_restart_receipt_detects_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "part.parquet"
            m = Path(d) / "part.json"
            p.write_bytes(b"one")
            record_part(p, m, 1, {"policy": "frozen"})
            self.assertTrue(valid_part(p, m, {"policy": "frozen"}))
            self.assertFalse(valid_part(p, m, {"policy": "changed"}))
            p.write_bytes(b"two")
            self.assertFalse(valid_part(p, m, {"policy": "frozen"}))

    def test_merge_retains_trailing_tabs_and_global_order(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "p00.candidate.tsv").write_text("S1-002\t\n", encoding="utf-8", newline="\n")
            (root / "p01.candidate.tsv").write_text("S1-001\tS2-1,S3-2\n", encoding="utf-8", newline="\n")
            result = assemble.merge(root, root / "candidate_pairs.tsv", "candidate")
            self.assertEqual(result["rows"], 2)
            self.assertEqual((root / "candidate_pairs.tsv").read_bytes(),
                b"source1_entity_id\tcandidate_entity_ids\nS1-001\tS2-1,S3-2\nS1-002\t\n")

    def test_independent_tsv_audit_detects_invalid_subset(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            selection = root / "selection.parquet"
            pq.write_table(pa.table({"entity_id": ["S1-001", "S1-002"],
                                     "split": ["p00", "p01"],
                                     "country_norm": ["france", "india"]}), selection)
            raw = root / "test_source1.tsv"
            raw.write_text("entity_id\tbusiness_name\tbusiness_address\tcountry\n"
                           "S1-001\tA\t\tFrance\nS1-002\tB\tC\tIndia\n", encoding="utf-8", newline="\n")
            candidate = root / "candidate_pairs.tsv"
            matching = root / "matching_results.tsv"
            candidate.write_text("source1_entity_id\tcandidate_entity_ids\nS1-001\tS2-1,S3-2\nS1-002\t\n", encoding="utf-8", newline="\n")
            matching.write_text("source1_entity_id\tmatched_entity_ids\nS1-001\tS3-2\nS1-002\t\n", encoding="utf-8", newline="\n")
            result = audit.read_pair(candidate, matching, selection, raw)
            self.assertEqual((result["rows"], result["candidate_ids"], result["matched_ids"]), (2, 2, 1))
            matching.write_text("source1_entity_id\tmatched_entity_ids\nS1-001\tS2-9\nS1-002\t\n", encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(RuntimeError, "outside candidates"):
                audit.read_pair(candidate, matching, selection, raw)

    def test_independent_audit_rejects_missing_tab(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            selection = root / "selection.parquet"
            pq.write_table(pa.table({"entity_id": ["S1-001"], "split": ["p00"],
                                     "country_norm": ["france"]}), selection)
            raw = root / "test_source1.tsv"
            raw.write_text("entity_id\tbusiness_name\tbusiness_address\tcountry\nS1-001\tA\t\tFrance\n", newline="\n")
            candidate = root / "candidate_pairs.tsv"
            matching = root / "matching_results.tsv"
            candidate.write_text("source1_entity_id\tcandidate_entity_ids\nS1-001\n", newline="\n")
            matching.write_text("source1_entity_id\tmatched_entity_ids\nS1-001\t\n", newline="\n")
            with self.assertRaisesRegex(RuntimeError, "Malformed output"):
                audit.read_pair(candidate, matching, selection, raw)

    def test_streaming_official_checks_on_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            raw = root / "test"
            raw.mkdir()
            (raw / "test_source1.tsv").write_text(
                "entity_id\tbusiness_name\tbusiness_address\tcountry\nS1-1\tA\t\tFrance\nS1-2\tB\t\tUS\n",
                encoding="utf-8", newline="\n")
            for source, target in ((2, "S2-1"), (3, "S3-1")):
                (raw / f"test_source{source}.tsv").write_text(
                    "entity_id\tbusiness_name\tbusiness_address\tcountry\n" + f"{target}\t\t\t\n",
                    encoding="utf-8", newline="\n")
            matching = root / "matching_results.tsv"
            candidate = root / "candidate_pairs.tsv"
            matching.write_text("source1_entity_id\tmatched_entity_ids\nS1-1\tS2-1\nS1-2\t\n",
                                encoding="utf-8", newline="\n")
            candidate.write_text("source1_entity_id\tcandidate_entity_ids\nS1-1\tS2-1,S3-1\nS1-2\t\n",
                                 encoding="utf-8", newline="\n")
            errors, warnings = validate_large_streaming(str(matching), str(candidate), str(raw), True)
            self.assertEqual((errors, warnings), ([], []))
            matching.write_text("source1_entity_id\tmatched_entity_ids\nS1-1\tS2-9\nS1-2\t\n",
                                encoding="utf-8", newline="\n")
            errors, _ = validate_large_streaming(str(matching), str(candidate), str(raw), True)
            self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
