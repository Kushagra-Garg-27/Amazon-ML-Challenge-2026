"""Tests for the deterministic split hash. Run:

    PYTHONUTF8=1 PYTHONPATH=code/business_entity_resolution/src \
        .venv/Scripts/python -m unittest -v test_split
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from er import split as sp  # noqa: E402


class TestAssign(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(sp.assign("S1-123", 42, 0.1), sp.assign("S1-123", 42, 0.1))

    def test_only_train_or_val(self):
        for i in range(1000):
            self.assertIn(sp.assign(f"S1-{i}", 7, 0.2), ("train", "val"))

    def test_seed_changes_partition(self):
        a = {i for i in range(2000) if sp.assign(f"S1-{i}", 1, 0.1) == "val"}
        b = {i for i in range(2000) if sp.assign(f"S1-{i}", 2, 0.1) == "val"}
        self.assertNotEqual(a, b)  # different seed -> different membership

    def test_fraction_is_approximately_right(self):
        n = 20000
        v = sum(sp.assign(f"S1-{i}", 42, 0.1) == "val" for i in range(n))
        self.assertAlmostEqual(v / n, 0.1, delta=0.01)  # ~2% relative tolerance

    def test_zero_and_full(self):
        self.assertEqual(sp.assign("x", 0, 0.0), "train")   # nothing < 0
        self.assertEqual(sp.assign("x", 0, 1.0), "val")     # everything < 1


if __name__ == "__main__":
    unittest.main()
