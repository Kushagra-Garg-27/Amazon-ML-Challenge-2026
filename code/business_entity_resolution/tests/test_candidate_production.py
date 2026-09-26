"""Production candidate-module tests for the frozen policy."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import duckdb

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "business_entity_resolution" / "src"))

from er.candidates.audit import compare_identity, partition_manifest, sha256_file
from er.candidates.materialize import materialize_partition
from er.candidates.policies import CandidatePolicy, load_policy
from er.candidates.ranking import EVIDENCE_ORDER_SQL


class TestProductionPolicy(unittest.TestCase):
    def test_frozen_policy_schema(self):
        policy = load_policy(ROOT / "work" / "final_candidate_policy.json")
        self.assertEqual((policy.s2_quota, policy.s3_quota), (50, 50))
        self.assertEqual(policy.heavy_sorted_cap, 100)
        self.assertTrue(EVIDENCE_ORDER_SQL.endswith("mid ASC"))

    def test_union_dedup_provenance_heavy_and_one_to_many(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); con = duckdb.connect()
            tables = {
                "sorted": [("s1", "S2-a"), ("s1", "S3-d"), ("s2", "S2-a")],
                "exact": [("s1", "S2-a")],
                "name": [("s1", "S2-b", 1), ("s1", "S2-x", 2)],
                "addr": [("s1", "S3-c", 1)],
                "heavy": [("s1", "S2-a", 1), ("s1", "S3-d", 2)],
            }
            con.execute("CREATE TABLE sorted(s1 VARCHAR,mid VARCHAR)")
            con.executemany("INSERT INTO sorted VALUES (?,?)", tables["sorted"])
            con.execute("CREATE TABLE exact(s1 VARCHAR,mid VARCHAR)")
            con.executemany("INSERT INTO exact VALUES (?,?)", tables["exact"])
            con.execute("CREATE TABLE name(s1 VARCHAR,mid VARCHAR,rsource INT)")
            con.executemany("INSERT INTO name VALUES (?,?,?)", tables["name"])
            con.execute("CREATE TABLE addr(s1 VARCHAR,mid VARCHAR,rsource INT)")
            con.executemany("INSERT INTO addr VALUES (?,?,?)", tables["addr"])
            con.execute("CREATE TABLE heavy(s1 VARCHAR,mid VARCHAR,rk INT)")
            con.executemany("INSERT INTO heavy VALUES (?,?,?)", tables["heavy"])
            paths = {}
            for name in tables:
                path = root / f"{name}.parquet"
                con.execute(f"COPY {name} TO '{path.as_posix()}' (FORMAT PARQUET)")
                paths[name] = path
            policy = CandidatePolicy(
                "fixture", "evidence_ranker_v1", 2000, 2000, 1, 1, 120, 1, 1,
                {"sorted_name": 1, "exact_address": 2, "name_token": 4,
                 "address_token": 8, "sister_expansion": 16},
                ("source1_entity_id", "target_entity_id"))
            inputs = {"sorted": paths["sorted"], "exact_address": paths["exact"],
                      "name_ranks": [paths["name"]], "address_ranks": [paths["addr"]],
                      "heavy_rank": paths["heavy"]}
            out = root / "part-00.parquet"
            self.assertEqual(materialize_partition(con, policy, inputs, 0, out), 4)
            rows = con.sql(f"SELECT * FROM read_parquet('{out.as_posix()}') ORDER BY 1,2").fetchall()
            self.assertEqual(rows, [("s1", "S2-a", 3), ("s1", "S2-b", 4),
                                    ("s1", "S3-c", 8), ("s2", "S2-a", 1)])
            con.close()

    def test_manifest_and_identity_comparison(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); con = duckdb.connect()
            con.execute("CREATE TABLE p(s1 VARCHAR,mid VARCHAR,prov INT)")
            con.execute("INSERT INTO p VALUES ('a','S2-1',1),('b','S3-2',8)")
            reference = root / "reference.parquet"
            con.execute(f"COPY (SELECT s1 source1_entity_id,mid target_entity_id,prov FROM p ORDER BY 1,2) TO '{reference.as_posix()}' (FORMAT PARQUET)")
            parts = []
            for i, key in enumerate(("a", "b")):
                part = root / f"part-{i:02d}.parquet"
                con.execute(f"COPY (SELECT s1 source1_entity_id,mid target_entity_id,prov FROM p WHERE s1='{key}') TO '{part.as_posix()}' (FORMAT PARQUET)")
                parts.append(part)
            con.close()
            manifest = partition_manifest(parts)
            self.assertEqual(manifest["row_count"], 2)
            self.assertEqual(len(manifest["partition_manifest_sha256"]), 64)
            self.assertEqual(compare_identity(reference, parts),
                             {"added": 0, "removed": 0, "duplicates": 0, "equal": True})

    def test_direct_gt_recall(self):
        c = duckdb.connect()
        c.execute("CREATE TABLE gt(s1 VARCHAR,mid VARCHAR); INSERT INTO gt VALUES ('1','a'),('1','b'),('2','c')")
        c.execute("CREATE TABLE cand(s1 VARCHAR,mid VARCHAR); INSERT INTO cand VALUES ('1','a'),('2','x')")
        recovered, total = c.sql("SELECT count(c.mid),count(*) FROM gt g LEFT JOIN cand c USING(s1,mid)").fetchone()
        self.assertEqual((recovered, total, recovered / total), (1, 3, 1 / 3))
        c.close()

    def test_production_reproduction_manifest(self):
        artifact_path = ROOT / "work" / "final_candidate_artifact_manifest.json"
        freeze_path = ROOT / "work" / "final_candidate_policy_manifest.json"
        if not artifact_path.exists() or not freeze_path.exists():
            self.skipTest("full frozen artifact absent")
        manifest = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["row_count"], 34_568_979)
        self.assertEqual(manifest["identity_comparison"],
                         {"added": 0, "removed": 0, "duplicates": 0, "equal": True})
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        self.assertTrue(freeze["freeze_gate"]["passed"])
        for item in (freeze["frozen_inputs"]["normalization"],
                     freeze["frozen_inputs"]["evaluator"],
                     freeze["frozen_inputs"]["split"], freeze["policy"]):
            self.assertEqual(sha256_file(ROOT / item["path"]), item["sha256"])


if __name__ == "__main__":
    unittest.main()
