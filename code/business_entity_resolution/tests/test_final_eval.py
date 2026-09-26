"""Release firewall and set metric regressions for the one-time evaluation."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import final_eval as fe


class TestFrozenRelease(unittest.TestCase):
    def test_preopen_checksums_and_order(self):
        state = fe.frozen_hashes()
        self.assertTrue(all(item.get("pass", True) for item in state.values()))
        self.assertEqual(state["feature_order"]["count"], 61)

    def test_release_gate_cannot_be_mutated(self):
        before = fe.digest(fe.W / "final_eval_release_gate_config.json")
        with self.assertRaisesRegex(RuntimeError, "already declared"):
            fe.gate_config()
        self.assertEqual(before, fe.digest(fe.W / "final_eval_release_gate_config.json"))

    def test_final_eval_not_in_development_selection(self):
        con = duckdb.connect()
        n = con.execute("""SELECT count(*) FROM read_parquet('work/model_development_samples.parquet') s
          JOIN read_parquet('work/matcher_split_manifest.parquet') m USING(entity_id)
          WHERE m.split='model_final_eval'""").fetchone()[0]
        con.close()
        self.assertEqual(n, 0)


class TestFrozenSetMetrics(unittest.TestCase):
    def setUp(self):
        self.table = pa.table({"truth_n":[0,2,2],"recovered_n":[0,2,1],
                               "pred_n":[0,2,1],"tp":[0,2,1]})

    def test_empty_and_multiple_matches_preserved(self):
        result = fe._aggregate(self.table)
        self.assertEqual(result["prediction_count_distribution"],{"0":1,"1":1,"2":1})
        self.assertEqual(result["singleton_accuracy"],1.0)
        self.assertEqual(result["predicted_pairs"],3)
        self.assertEqual(result["complete_recovery_s1"],1)
        self.assertEqual(result["partial_recovery_s1"],1)

    def test_bootstrap_is_deterministic_at_s1_level(self):
        config = {"bootstrap":{"seed": 29,"replicates": 30}}
        country = np.array(["india","india","us"])
        with tempfile.TemporaryDirectory() as folder:
            a = fe._bootstrap(self.table,country,config,Path(folder)/"a.parquet")
            b = fe._bootstrap(self.table,country,config,Path(folder)/"b.parquet")
        self.assertEqual(a["intervals"],b["intervals"])

    def test_unseen_key_definition_and_grouped_overlap(self):
        con = duckdb.connect()
        con.execute("CREATE TABLE fit(name VARCHAR,address VARCHAR); INSERT INTO fit VALUES ('a','x'),('b','y')")
        con.execute("CREATE TABLE final(name VARCHAR,address VARCHAR); INSERT INTO final VALUES ('a','z'),('c','x'),('d','z')")
        rows = con.execute("""SELECT name,
          NOT EXISTS(SELECT 1 FROM fit WHERE fit.name=final.name) unseen_name,
          NOT EXISTS(SELECT 1 FROM fit WHERE fit.address=final.address) unseen_address
          FROM final ORDER BY name""").fetchall()
        con.close()
        self.assertEqual(rows,[("a",False,True),("c",True,False),("d",True,True)])


if __name__ == "__main__":
    unittest.main()
