"""Deterministic tests for candidate-generation logic (Step 18).

Locks the invariants the candidate policy relies on: exact⊆sorted containment,
identity dedup with provenance-bitmask union, one-to-many preservation under the
per-S1 cap, IDF-weighted cap determinism, deterministic top-K pruning monotonicity,
and structural invariants of the emitted provenance artifact (when present). Run:

    PYTHONUTF8=1 PYTHONPATH=code/business_entity_resolution/src \
        .venv/Scripts/python -m unittest -v test_candidates
"""
import os
import sys
import unittest

import duckdb

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from er import normalize as nz  # noqa: E402

ART = os.path.join(os.path.dirname(__file__), "..", "..", "..", "work",
                   "final_candidate_provenance.parquet")


class TestContainment(unittest.TestCase):
    """exact-name ⊆ sorted-name is a construction invariant of normalize."""

    CASES = ["Acme Pvt Ltd", "acme private limited", "Beta & Co", "Beta and Company",
             "Global Tech Solutions", "solutions tech global", "  --Foo Bar  ", "Foo   Bar"]

    def test_nosuffix_equality_implies_sorted_equality(self):
        for a in self.CASES:
            for b in self.CASES:
                if nz.name_nosuffix(a) == nz.name_nosuffix(b):
                    self.assertEqual(nz.name_sorted(a), nz.name_sorted(b),
                                     f"{a!r} vs {b!r}: equal nosuffix, unequal sorted")

    def test_sorted_is_word_order_invariant(self):
        self.assertEqual(nz.name_sorted("Global Tech Solutions"),
                         nz.name_sorted("solutions tech global"))

    def test_sorted_dedups_tokens(self):
        # name_sorted uses set() -> duplicate tokens collapse
        self.assertEqual(nz.name_sorted("alpha alpha beta"), nz.name_sorted("beta alpha"))


class TestFinalStreamSQL(unittest.TestCase):
    """Replicate the exact deterministic SQL of the final stream on a tiny fixture."""

    def setUp(self):
        self.con = duckdb.connect()
        # three passes producing overlapping (s1,mid) identities; bits 1,2,4
        self.con.execute("""CREATE TABLE cand_a(s1 VARCHAR, mid VARCHAR);
            INSERT INTO cand_a VALUES ('S1-1','S2-1'),('S1-1','S2-2'),('S1-2','S3-9');
            CREATE TABLE cand_b(s1 VARCHAR, mid VARCHAR);
            INSERT INTO cand_b VALUES ('S1-1','S2-1'),('S1-2','S3-9'),('S1-2','S2-5');
            CREATE TABLE cand_c(s1 VARCHAR, mid VARCHAR);
            INSERT INTO cand_c VALUES ('S1-1','S2-1');""")
        parts = [("cand_a", 1), ("cand_b", 2), ("cand_c", 4)]
        usql = " UNION ALL ".join(f"SELECT s1,mid,{b} b FROM {t}" for t, b in parts)
        self.con.execute(f"""CREATE TABLE final_cand AS
            SELECT s1, mid, bit_or(b)::INT prov FROM ({usql}) GROUP BY 1,2""")

    def tearDown(self):
        self.con.close()

    def test_identity_dedup(self):
        # 4 distinct (s1,mid) identities from 7 pass-rows
        n, dup = self.con.sql("""SELECT count(*), count(*)-count(DISTINCT (s1,mid))
            FROM final_cand""").fetchone()
        self.assertEqual(n, 4)
        self.assertEqual(dup, 0)

    def test_provenance_bitmask_union(self):
        # (S1-1,S2-1) seen in a,b,c -> prov 1|2|4 = 7; (S1-2,S3-9) in a,b -> 3
        self.assertEqual(self.con.sql(
            "SELECT prov FROM final_cand WHERE s1='S1-1' AND mid='S2-1'").fetchone()[0], 7)
        self.assertEqual(self.con.sql(
            "SELECT prov FROM final_cand WHERE s1='S1-2' AND mid='S3-9'").fetchone()[0], 3)
        self.assertEqual(self.con.sql(
            "SELECT prov FROM final_cand WHERE s1='S1-1' AND mid='S2-2'").fetchone()[0], 1)

    def test_provenance_never_zero(self):
        self.assertEqual(self.con.sql(
            "SELECT count(*) FROM final_cand WHERE prov=0 OR prov IS NULL").fetchone()[0], 0)

    def test_one_to_many_preserved(self):
        # both S1-1 and S1-2 keep multiple targets (blocking must not collapse to top-1)
        rows = dict(self.con.sql(
            "SELECT s1, count(*) FROM final_cand GROUP BY 1").fetchall())
        self.assertGreaterEqual(rows["S1-1"], 2)
        self.assertGreaterEqual(rows["S1-2"], 2)

    def test_topk_pruning_is_monotone_and_deterministic(self):
        self.con.execute("""CREATE TABLE ranked AS
            SELECT s1, mid, prov, row_number() OVER (PARTITION BY s1
              ORDER BY bit_count(prov) DESC, hash(mid)) rk FROM final_cand""")
        k1 = set(self.con.sql("SELECT s1,mid FROM ranked WHERE rk<=1").fetchall())
        k2 = set(self.con.sql("SELECT s1,mid FROM ranked WHERE rk<=2").fetchall())
        self.assertTrue(k1 <= k2)  # monotone: smaller cap ⊆ larger cap
        # determinism: recompute identical ranks
        again = set(self.con.sql("""SELECT s1,mid FROM (
            SELECT s1,mid, row_number() OVER (PARTITION BY s1
              ORDER BY bit_count(prov) DESC, hash(mid)) rk FROM final_cand) WHERE rk<=1
            """).fetchall())
        self.assertEqual(k1, again)
        # rank-1 per S1 is the highest-provenance-count candidate
        top = self.con.sql("SELECT mid FROM ranked WHERE s1='S1-1' AND rk=1").fetchone()[0]
        self.assertEqual(top, "S2-1")  # prov=7 (3 bits) beats prov=1 candidates


class TestIDFCap(unittest.TestCase):
    """Per-S1 token cap ranks by IDF-weighted shared-token specificity (sum 1/df_t).
    A target sharing one RARE token must outrank a target sharing several common tokens."""

    def setUp(self):
        self.con = duckdb.connect()
        # surviving-token df table: 'rare' df=1, 'common1'/'common2' df=1000
        self.con.execute("""CREATE TABLE surv(cc VARCHAR, tok VARCHAR, df_t BIGINT);
            INSERT INTO surv VALUES ('US','rare',1),('US','common1',1000),('US','common2',1000);
            CREATE TABLE s1tok(entity_id VARCHAR, cc VARCHAR, tok VARCHAR);
            INSERT INTO s1tok VALUES ('S1-1','US','rare'),('S1-1','US','common1'),('S1-1','US','common2');
            CREATE TABLE tgtok(entity_id VARCHAR, cc VARCHAR, tok VARCHAR);
            -- T_rare shares only 'rare'; T_common shares both common tokens
            INSERT INTO tgtok VALUES ('S2-1','US','rare'),
                ('S2-2','US','common1'),('S2-2','US','common2');""")

    def tearDown(self):
        self.con.close()

    def _cand(self, cap):
        return self.con.sql(f"""SELECT mid FROM (
            SELECT s1, mid, sh, score,
                   row_number() OVER (PARTITION BY s1 ORDER BY score DESC, sh DESC, hash(mid)) rk
            FROM (SELECT s.entity_id s1, t.entity_id mid, count(*) sh, sum(1.0/v.df_t) score
                  FROM s1tok s JOIN surv v ON s.cc=v.cc AND s.tok=v.tok
                  JOIN tgtok t ON t.cc=s.cc AND t.tok=s.tok GROUP BY 1,2)
            ) WHERE rk<={cap} ORDER BY rk""").fetchall()

    def test_rare_token_outranks_common_pair(self):
        # score(S2-1)=1/1=1.0 ; score(S2-2)=1/1000+1/1000=0.002 -> rare wins at cap=1
        self.assertEqual(self._cand(1), [("S2-1",)])

    def test_cap_is_deterministic(self):
        self.assertEqual(self._cand(2), self._cand(2))

    def test_cap_bounds_and_monotone(self):
        self.assertEqual(len(self._cand(1)), 1)
        self.assertEqual([m for m, in self._cand(2)], ["S2-1", "S2-2"])


class TestArtifactInvariants(unittest.TestCase):
    """Structural invariants of the emitted provenance artifact (skipped if not built)."""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(ART):
            raise unittest.SkipTest("final_candidate_provenance.parquet not built yet")
        cls.con = duckdb.connect()
        cls.con.execute("SET memory_limit='1GB'; SET threads=4;")
        cls.p = f"read_parquet('{ART.replace(os.sep, '/')}')"

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "con"):
            cls.con.close()

    def test_no_duplicate_identity(self):
        n, d = self.con.sql(f"""SELECT count(*),
            count(*)-count(DISTINCT (source1_entity_id,target_entity_id)) FROM {self.p}
            """).fetchone()
        self.assertEqual(d, 0, f"{d} duplicate (s1,target) identities in {n} rows")

    def test_provenance_in_range(self):
        # bits sorted=1, addr=2, nametok=4, addrtok=8 -> prov in [1,15], never 0
        bad = self.con.sql(f"SELECT count(*) FROM {self.p} WHERE prov<1 OR prov>15").fetchone()[0]
        self.assertEqual(bad, 0)

    def test_namespaces(self):
        bad = self.con.sql(f"""SELECT count(*) FROM {self.p}
            WHERE source1_entity_id NOT LIKE 'S1-%'
               OR NOT (target_entity_id LIKE 'S2-%' OR target_entity_id LIKE 'S3-%')
            """).fetchone()[0]
        self.assertEqual(bad, 0)

    def test_no_self_or_s1_targets(self):
        bad = self.con.sql(
            f"SELECT count(*) FROM {self.p} WHERE target_entity_id LIKE 'S1-%'").fetchone()[0]
        self.assertEqual(bad, 0)


if __name__ == "__main__":
    unittest.main()

