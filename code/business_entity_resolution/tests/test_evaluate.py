"""Tests for the macro-F_0.5 evaluator. Run:

    PYTHONUTF8=1 PYTHONPATH=code/business_entity_resolution/src \
        .venv/Scripts/python -m unittest -v er_tests.test_evaluate
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from er import evaluate as ev  # noqa: E402


class TestFBetaEntity(unittest.TestCase):
    def test_spec_worked_example(self):
        # Spec (ProblemStatement.txt:208): P=2/3, R=1.0 -> 0.714
        pred = {"S2-00047", "S2-00193", "S3-00812"}
        truth = {"S2-00047", "S3-00812"}
        self.assertAlmostEqual(ev.f_beta_entity(pred, truth), 0.714, places=3)

    def test_perfect_match(self):
        s = {"S2-1", "S3-2"}
        self.assertEqual(ev.f_beta_entity(s, set(s)), 1.0)

    def test_singleton_correct(self):
        self.assertEqual(ev.f_beta_entity(set(), set()), 1.0)

    def test_singleton_false_merge(self):
        self.assertEqual(ev.f_beta_entity({"S2-1"}, set()), 0.0)

    def test_truth_nonempty_pred_empty(self):
        self.assertEqual(ev.f_beta_entity(set(), {"S2-1"}), 0.0)

    def test_disjoint(self):
        self.assertEqual(ev.f_beta_entity({"S2-9"}, {"S2-1"}), 0.0)

    def test_precision_weighted_over_recall(self):
        # Same |symmetric difference|, but F_0.5 must prefer higher precision.
        truth = {"a", "b", "c", "d"}
        high_prec = ev.f_beta_entity({"a", "b", "c"}, truth)          # P=1.0 R=0.75
        high_rec = ev.f_beta_entity({"a", "b", "c", "d", "e"}, truth)  # P=0.8 R=1.0
        self.assertGreater(high_prec, high_rec)

    def test_formula_matches_manual(self):
        pred, truth = {"a", "b", "x"}, {"a", "b", "c", "d"}  # TP2 P=2/3 R=1/2
        p, r = 2 / 3, 1 / 2
        expected = 1.25 * p * r / (0.25 * p + r)
        self.assertAlmostEqual(ev.f_beta_entity(pred, truth), expected, places=12)


class TestMacroAndDiagnostics(unittest.TestCase):
    def test_macro_average_includes_singletons(self):
        truth = {"S1-1": {"S2-1"}, "S1-2": set(), "S1-3": {"S3-9"}}
        pred = {"S1-1": {"S2-1"}, "S1-2": set(), "S1-3": {"S2-8"}}  # 1.0,1.0,0.0
        self.assertAlmostEqual(ev.macro_f_beta(pred, truth), 2.0 / 3.0, places=12)

    def test_universe_counts_missing_rows(self):
        # An S1 in the universe but absent from pred is scored (as empty pred).
        truth = {"S1-1": {"S2-1"}}
        pred = {}
        self.assertEqual(ev.macro_f_beta(pred, truth, entities=["S1-1"]), 0.0)

    def test_micro_pair_pr(self):
        truth = {"S1-1": {"a", "b"}, "S1-2": {"c"}}
        pred = {"S1-1": {"a", "x"}, "S1-2": {"c"}}
        d = ev.micro_pair_pr(pred, truth)
        self.assertEqual((d["pairs_tp"], d["pairs_fp"], d["pairs_fn"]), (2, 1, 1))
        self.assertAlmostEqual(d["micro_precision"], 2 / 3, places=12)
        self.assertAlmostEqual(d["micro_recall"], 2 / 3, places=12)

    def test_report_shape_and_country(self):
        truth = {"S1-1": {"S2-1"}, "S1-2": set()}
        pred = {"S1-1": {"S2-1"}, "S1-2": set()}
        rep = ev.report(pred, truth, country={"S1-1": "US", "S1-2": "India"})
        self.assertEqual(rep["n_entities"], 2)
        self.assertEqual(rep["macro_f0_5"], 1.0)
        self.assertEqual(rep["n_singleton_true"], 1)
        self.assertEqual(rep["n_singleton_correct"], 1)
        self.assertIn("US", rep["per_country_macro_f0_5"])


if __name__ == "__main__":
    unittest.main()
