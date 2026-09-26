"""Foundation item 6 — synthetic fixture covering the hard cases end-to-end.

The mini fixture (tests/fixtures/mini/) exercises: singleton (S1-004), one-to-many
across sources (S1-001 -> S2+S3), multiple matches within ONE source (S1-002 -> two S2),
cross-script Devanagari (S1-003), unseen country Brazil (S1-005), missing address
(S1-006 / S2-005 blank), and duplicate-looking distinct records (S1-007/008 same name,
different entities). This test scores predictions with the entity-level evaluator AND
runs the OFFICIAL validator on a generated submission to confirm PASS/FAIL semantics.

Run:
    PYTHONUTF8=1 .venv/Scripts/python -m unittest -v test_fixture   (via discover)
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from er import evaluate as ev  # noqa: E402

MINI = os.path.join(HERE, "fixtures", "mini")
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
VALIDATOR = os.path.join(REPO, "utils", "validate_submission.py")


def write_matching(path, mapping):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("source1_entity_id\tmatched_entity_ids\n")
        for s1 in sorted(mapping):
            f.write(f"{s1}\t{','.join(sorted(mapping[s1]))}\n")


class TestFixtureScoring(unittest.TestCase):
    def setUp(self):
        self.truth = ev.read_results_tsv(os.path.join(MINI, "ground_truth.tsv"))

    def test_truth_parsed(self):
        self.assertEqual(self.truth["S1-001"], {"S2-001", "S3-001"})
        self.assertEqual(self.truth["S1-002"], {"S2-002", "S2-003"})  # two within S2
        self.assertEqual(self.truth["S1-004"], set())                 # singleton

    def test_perfect_prediction_scores_one(self):
        self.assertEqual(ev.macro_f_beta(self.truth, self.truth), 1.0)

    def test_perturbed_prediction_known_value(self):
        pred = {k: set(v) for k, v in self.truth.items()}
        pred["S1-001"] = {"S2-001"}      # miss S3-001: P=1 R=.5 -> F0.5=0.833333
        pred["S1-004"] = {"S2-099"}      # false merge on a true singleton -> 0.0
        macro = ev.macro_f_beta(pred, self.truth)
        self.assertAlmostEqual(macro, (0.8333333333 + 0.0 + 6) / 8, places=6)


class TestOfficialValidator(unittest.TestCase):
    def test_valid_submission_passes(self):
        truth = ev.read_results_tsv(os.path.join(MINI, "ground_truth.tsv"))
        with tempfile.TemporaryDirectory() as td:
            m = os.path.join(td, "matching_results.tsv")
            c = os.path.join(td, "candidate_pairs.tsv")
            write_matching(m, truth)
            # candidates: a superset (add an extra plausible candidate for S1-007)
            cand = {k: set(v) for k, v in truth.items()}
            cand["S1-007"] = cand["S1-007"] | {"S2-007"}
            with open(c, "w", encoding="utf-8", newline="") as f:
                f.write("source1_entity_id\tcandidate_entity_ids\n")
                for s1 in sorted(cand):
                    f.write(f"{s1}\t{','.join(sorted(cand[s1]))}\n")
            r = subprocess.run(
                [sys.executable, VALIDATOR, "--matching", m, "--candidate", c,
                 "--test-dir", MINI],
                capture_output=True, text=True, env={**os.environ, "PYTHONUTF8": "1"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("PASS", r.stdout)

    def test_space_after_comma_fails(self):
        truth = ev.read_results_tsv(os.path.join(MINI, "ground_truth.tsv"))
        with tempfile.TemporaryDirectory() as td:
            m = os.path.join(td, "matching_results.tsv")
            c = os.path.join(td, "candidate_pairs.tsv")
            with open(m, "w", encoding="utf-8", newline="") as f:
                f.write("source1_entity_id\tmatched_entity_ids\n")
                for s1 in sorted(truth):
                    # inject a space after comma -> id no longer starts with S2-/S3-
                    f.write(f"{s1}\t{', '.join(sorted(truth[s1]))}\n")
            with open(c, "w", encoding="utf-8", newline="") as f:
                f.write("source1_entity_id\tcandidate_entity_ids\n")
                for s1 in sorted(truth):
                    f.write(f"{s1}\t{','.join(sorted(truth[s1]))}\n")
            r = subprocess.run(
                [sys.executable, VALIDATOR, "--matching", m, "--candidate", c,
                 "--test-dir", MINI],
                capture_output=True, text=True, env={**os.environ, "PYTHONUTF8": "1"})
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("FAIL", r.stdout)


class TestSingletonRepresentation(unittest.TestCase):
    """The only submission-accepted singleton form is 'S1-xxx\\t' (trailing tab,
    empty field). Verified against the official validator 2026-09-25: a bare no-tab
    row is rejected as 'malformed row (no tab)' and its S1 id is dropped (then reported
    missing). The real train_ground_truth.tsv uses the trailing-tab form. The evaluator
    reader must match: reject bare rows, accept the trailing-tab singleton."""

    def _write(self, path, header, rows):
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(header + "\n")
            for line in rows:
                f.write(line + "\n")

    def test_evaluator_rejects_bare_no_tab_row(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "gt.tsv")
            self._write(p, "source1_entity_id\tmatched_entity_ids",
                        ["S1-001\tS2-001", "S1-004"])  # bare singleton
            with self.assertRaises(ValueError):
                ev.read_results_tsv(p)

    def test_evaluator_accepts_trailing_tab_singleton(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "gt.tsv")
            self._write(p, "source1_entity_id\tmatched_entity_ids",
                        ["S1-001\tS2-001", "S1-004\t"])  # accepted singleton form
            got = ev.read_results_tsv(p)
            self.assertEqual(got["S1-004"], set())
            self.assertEqual(got["S1-001"], {"S2-001"})

    def _run_validator(self, td, singleton_line):
        truth = ev.read_results_tsv(os.path.join(MINI, "ground_truth.tsv"))
        m = os.path.join(td, "matching_results.tsv")
        c = os.path.join(td, "candidate_pairs.tsv")
        for path, col in ((m, "matched_entity_ids"), (c, "candidate_entity_ids")):
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(f"source1_entity_id\t{col}\n")
                for s1 in sorted(truth):
                    ids = truth[s1]
                    if ids:
                        f.write(f"{s1}\t{','.join(sorted(ids))}\n")
                    else:
                        f.write(singleton_line.format(s1=s1) + "\n")
        return subprocess.run(
            [sys.executable, VALIDATOR, "--matching", m, "--candidate", c,
             "--test-dir", MINI],
            capture_output=True, text=True, env={**os.environ, "PYTHONUTF8": "1"})

    def test_validator_accepts_trailing_tab_rejects_bare(self):
        with tempfile.TemporaryDirectory() as td:
            ok = self._run_validator(td, "{s1}\t")     # trailing tab
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            self.assertIn("PASS", ok.stdout)
        with tempfile.TemporaryDirectory() as td:
            bad = self._run_validator(td, "{s1}")       # bare, no tab
            self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
            self.assertIn("malformed row (no tab)", bad.stdout)


if __name__ == "__main__":
    unittest.main()
