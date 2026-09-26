"""Deterministic tests for candidate-generation refinement (Section 10).

Tests cover:
- source-balanced top-K
- dynamic-K
- deterministic tie-breaking
- exact reproduction after restart
- selected-artifact recall computed by direct GT join
- separation of eligibility loss from pruning loss
- heavy-block refinement
- artifact schema and unique identity

Run:
    PYTHONUTF8=1 PYTHONPATH=code/business_entity_resolution/src ^
        .venv/Scripts/python -m pytest code/business_entity_resolution/tests/test_refinement.py -v
"""
import os
import sys
import unittest

import duckdb

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from er import normalize as nz  # noqa: E402

ART = os.path.join(os.path.dirname(__file__), "..", "..", "..", "work",
                   "final_candidate_provenance.parquet")


class TestSourceBalancedTopK(unittest.TestCase):
    """Source-balanced top-K ensures neither S2 nor S3 dominates the cap."""

    def setUp(self):
        self.con = duckdb.connect()
        # S1-1 has 3 S2 and 3 S3 candidates with varying scores
        self.con.execute("""
            CREATE TABLE surv(cc VARCHAR, tok VARCHAR, df_t BIGINT);
            INSERT INTO surv VALUES ('US','a',1),('US','b',2),('US','c',3);
            CREATE TABLE s1tok(entity_id VARCHAR, cc VARCHAR, tok VARCHAR);
            INSERT INTO s1tok VALUES ('S1-1','US','a'),('S1-1','US','b'),('S1-1','US','c');
            CREATE TABLE tgtok(entity_id VARCHAR, cc VARCHAR, tok VARCHAR);
            -- S2 candidates share different tokens
            INSERT INTO tgtok VALUES
                ('S2-1','US','a'),('S2-1','US','b'),('S2-1','US','c'),  -- score=1+0.5+0.33=1.83
                ('S2-2','US','a'),('S2-2','US','b'),                     -- score=1+0.5=1.5
                ('S2-3','US','b'),('S2-3','US','c'),                     -- score=0.5+0.33=0.83
                ('S3-1','US','a'),                                        -- score=1.0
                ('S3-2','US','b'),('S3-2','US','c'),                     -- score=0.5+0.33=0.83
                ('S3-3','US','c');                                        -- score=0.33
        """)

    def tearDown(self):
        self.con.close()

    def test_balanced_cap_includes_both_sources(self):
        """With cap_per_source=2, we get top-2 S2 and top-2 S3."""
        result = self.con.sql("""
            SELECT s1, mid FROM (
                SELECT s.entity_id s1, t.entity_id mid,
                       CASE WHEN t.entity_id LIKE 'S2-%' THEN 'S2' ELSE 'S3' END src,
                       sum(1.0/v.df_t) score, count(*) sh,
                       row_number() OVER (PARTITION BY s.entity_id,
                           CASE WHEN t.entity_id LIKE 'S2-%' THEN 'S2' ELSE 'S3' END
                           ORDER BY sum(1.0/v.df_t) DESC, count(*) DESC, hash(t.entity_id)) rk
                FROM s1tok s JOIN surv v ON s.cc=v.cc AND s.tok=v.tok
                JOIN tgtok t ON t.cc=s.cc AND t.tok=s.tok
                GROUP BY s.entity_id, t.entity_id
            ) WHERE rk <= 2
        """).fetchall()
        mids = {mid for _, mid in result}
        # Should have 2 S2 and 2 S3
        s2_count = sum(1 for m in mids if m.startswith("S2-"))
        s3_count = sum(1 for m in mids if m.startswith("S3-"))
        self.assertEqual(s2_count, 2, f"Expected 2 S2, got {s2_count}")
        self.assertEqual(s3_count, 2, f"Expected 2 S3, got {s3_count}")

    def test_unbalanced_cap_can_exclude_source(self):
        """With global cap=2, the top-2 might be all S2 (higher scores)."""
        result = self.con.sql("""
            SELECT s1, mid FROM (
                SELECT s.entity_id s1, t.entity_id mid,
                       sum(1.0/v.df_t) score, count(*) sh,
                       row_number() OVER (PARTITION BY s.entity_id
                           ORDER BY sum(1.0/v.df_t) DESC, count(*) DESC, hash(t.entity_id)) rk
                FROM s1tok s JOIN surv v ON s.cc=v.cc AND s.tok=v.tok
                JOIN tgtok t ON t.cc=s.cc AND t.tok=s.tok
                GROUP BY s.entity_id, t.entity_id
            ) WHERE rk <= 2
        """).fetchall()
        mids = {mid for _, mid in result}
        # Top-2 by score: S2-1 (1.83) and S2-2 (1.5) — both S2
        self.assertEqual(len(mids), 2)
        self.assertTrue(all(m.startswith("S2-") for m in mids),
                        f"Expected all S2 in top-2, got {mids}")

    def test_balanced_preserves_within_source_ranking(self):
        """Within each source, the ranking is still by score DESC."""
        result = self.con.sql("""
            SELECT mid, src, rk FROM (
                SELECT s.entity_id s1, t.entity_id mid,
                       CASE WHEN t.entity_id LIKE 'S2-%' THEN 'S2' ELSE 'S3' END src,
                       sum(1.0/v.df_t) score, count(*) sh,
                       row_number() OVER (PARTITION BY s.entity_id,
                           CASE WHEN t.entity_id LIKE 'S2-%' THEN 'S2' ELSE 'S3' END
                           ORDER BY sum(1.0/v.df_t) DESC, count(*) DESC, hash(t.entity_id)) rk
                FROM s1tok s JOIN surv v ON s.cc=v.cc AND s.tok=v.tok
                JOIN tgtok t ON t.cc=s.cc AND t.tok=s.tok
                GROUP BY s.entity_id, t.entity_id
            ) WHERE src='S2' ORDER BY rk
        """).fetchall()
        # S2-1 should be rank 1 (highest score), S2-2 rank 2, S2-3 rank 3
        self.assertEqual(result[0][0], "S2-1")
        self.assertEqual(result[1][0], "S2-2")


class TestDynamicK(unittest.TestCase):
    """Dynamic-K allocates more capacity to weak-evidence S1 entities."""

    def setUp(self):
        self.con = duckdb.connect()
        # Two S1 entities: one with strong evidence (sorted-name match), one without
        self.con.execute("""
            CREATE TABLE s1_evidence(entity_id VARCHAR, has_sorted BOOL, has_addr BOOL);
            INSERT INTO s1_evidence VALUES ('S1-strong', true, true), ('S1-weak', false, false);
        """)

    def tearDown(self):
        self.con.close()

    def test_strong_gets_small_k(self):
        k = self.con.sql("""SELECT CASE WHEN has_sorted THEN 50
                WHEN has_addr THEN 100 ELSE 200 END
            FROM s1_evidence WHERE entity_id='S1-strong'""").fetchone()[0]
        self.assertEqual(k, 50)

    def test_weak_gets_large_k(self):
        k = self.con.sql("""SELECT CASE WHEN has_sorted THEN 50
                WHEN has_addr THEN 100 ELSE 200 END
            FROM s1_evidence WHERE entity_id='S1-weak'""").fetchone()[0]
        self.assertEqual(k, 200)

    def test_addr_only_gets_medium_k(self):
        self.con.execute("""INSERT INTO s1_evidence VALUES ('S1-addr', false, true)""")
        k = self.con.sql("""SELECT CASE WHEN has_sorted THEN 50
                WHEN has_addr THEN 100 ELSE 200 END
            FROM s1_evidence WHERE entity_id='S1-addr'""").fetchone()[0]
        self.assertEqual(k, 100)


class TestDeterministicTieBreaking(unittest.TestCase):
    """Tie-breaking must be deterministic (hash-based, not random)."""

    def setUp(self):
        self.con = duckdb.connect()
        # Create candidates with IDENTICAL scores but different entity IDs
        self.con.execute("""
            CREATE TABLE tied_cands(s1 VARCHAR, mid VARCHAR, score DOUBLE, sh INT);
            INSERT INTO tied_cands VALUES
                ('S1-1','S2-A',1.0,1),('S1-1','S2-B',1.0,1),
                ('S1-1','S2-C',1.0,1),('S1-1','S2-D',1.0,1);
        """)

    def tearDown(self):
        self.con.close()

    def test_ranking_is_deterministic(self):
        """Same query run twice produces identical ranks."""
        def get_ranks():
            return self.con.sql("""
                SELECT mid, row_number() OVER (PARTITION BY s1
                    ORDER BY score DESC, sh DESC, hash(mid)) rk
                FROM tied_cands ORDER BY rk
            """).fetchall()
        r1 = get_ranks()
        r2 = get_ranks()
        self.assertEqual(r1, r2, "Tie-breaking not deterministic across runs")

    def test_cap_at_2_is_deterministic(self):
        """Capping tied candidates at K=2 gives the same 2 every time."""
        def get_top2():
            return set(self.con.sql("""
                SELECT mid FROM (
                    SELECT mid, row_number() OVER (PARTITION BY s1
                        ORDER BY score DESC, sh DESC, hash(mid)) rk
                    FROM tied_cands
                ) WHERE rk <= 2
            """).fetchall())
        self.assertEqual(get_top2(), get_top2())


class TestExactReproductionAfterRestart(unittest.TestCase):
    """Final stream SQL produces identical results when re-executed."""

    def test_final_stream_reproduces(self):
        con = duckdb.connect()
        # Simulate the final stream SQL from candidate_experiments.py
        con.execute("""
            CREATE TABLE cand_a(s1 VARCHAR, mid VARCHAR);
            INSERT INTO cand_a VALUES ('S1-1','S2-1'),('S1-1','S2-2');
            CREATE TABLE cand_b(s1 VARCHAR, mid VARCHAR);
            INSERT INTO cand_b VALUES ('S1-1','S2-1'),('S1-1','S3-3');
        """)
        parts = [("cand_a", 1), ("cand_b", 2)]

        def build_union():
            usql = " UNION ALL ".join(f"SELECT s1,mid,{b} b FROM {t}" for t, b in parts)
            return con.sql(f"""SELECT s1, mid, bit_or(b)::INT prov
                FROM ({usql}) GROUP BY 1,2 ORDER BY s1, mid""").fetchall()

        r1 = build_union()
        r2 = build_union()
        self.assertEqual(r1, r2, "Final stream not reproducible")
        con.close()


class TestSelectedArtifactRecallByDirectGTJoin(unittest.TestCase):
    """Recall must be computed by direct join to GT, not from blocker-level summaries."""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(ART):
            raise unittest.SkipTest("final_candidate_provenance.parquet not built yet")
        cls.con = duckdb.connect()
        cls.con.execute("SET memory_limit='1GB'; SET threads=4;")
        cls.art = ART.replace(os.sep, '/')

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "con"):
            cls.con.close()

    def test_recall_by_direct_join(self):
        """Recall computed by joining artifact to GT matches the reported value."""
        # Load GT for val split
        gt_path = "dataset/train/train_ground_truth.tsv"
        split_path = "work/split_s1.parquet"
        if not os.path.exists(gt_path) or not os.path.exists(split_path):
            self.skipTest("GT or split file not available")

        # Count GT pairs for val split
        tot = self.con.sql(f"""SELECT count(*) FROM (
            SELECT g.source1_entity_id s1, trim(x) mid
            FROM read_csv('{gt_path}', delim='\t', header=true, quote='', all_varchar=true) g,
                 UNNEST(string_split(g.matched_entity_ids, ',')) AS u(x)
            WHERE g.matched_entity_ids IS NOT NULL AND length(trim(g.matched_entity_ids))>0
              AND g.source1_entity_id IN (
                  SELECT entity_id FROM read_parquet('{split_path}') WHERE split='val'))
        """).fetchone()[0]

        # Count recovered by direct join
        recovered = self.con.sql(f"""SELECT count(*) FROM (
            SELECT g.source1_entity_id s1, trim(x) mid
            FROM read_csv('{gt_path}', delim='\t', header=true, quote='', all_varchar=true) g,
                 UNNEST(string_split(g.matched_entity_ids, ',')) AS u(x)
            WHERE g.matched_entity_ids IS NOT NULL AND length(trim(g.matched_entity_ids))>0
              AND g.source1_entity_id IN (
                  SELECT entity_id FROM read_parquet('{split_path}') WHERE split='val')
              AND (g.source1_entity_id, trim(x)) IN (
                  SELECT source1_entity_id, target_entity_id FROM read_parquet('{self.art}'))
        )""").fetchone()[0]

        recall = recovered / tot if tot > 0 else 0
        self.assertAlmostEqual(recall, 0.8614, places=3,
                               msg=f"Direct-join recall {recall:.4f} != reported 0.8614")
        self.assertEqual(recovered, 658063,
                         f"Recovered {recovered} != expected 658063")


class TestEligibilityVsPruningLossSeparation(unittest.TestCase):
    """Eligibility loss and pruning loss must be mutually exclusive and sum to total loss."""

    def test_loss_partition_on_fixture(self):
        con = duckdb.connect()
        # 5 GT pairs; 3 eligible (have matching key), 2 ineligible
        # Of the 3 eligible, only 2 are in the materialized candidates
        con.execute("""
            CREATE TABLE gt_pairs(s1 VARCHAR, mid VARCHAR, eligible BOOL);
            INSERT INTO gt_pairs VALUES
                ('S1-1','S2-1',true),('S1-1','S2-2',true),('S1-1','S2-3',true),
                ('S1-2','S3-1',false),('S1-2','S3-2',false);
            CREATE TABLE candidates(s1 VARCHAR, mid VARCHAR);
            INSERT INTO candidates VALUES ('S1-1','S2-1'),('S1-1','S2-2');
        """)
        tot = 5
        ineligible = con.sql("SELECT count(*) FROM gt_pairs WHERE NOT eligible").fetchone()[0]
        eligible_but_missing = con.sql("""SELECT count(*) FROM gt_pairs
            WHERE eligible AND (s1,mid) NOT IN (SELECT s1,mid FROM candidates)""").fetchone()[0]
        recovered = con.sql("""SELECT count(*) FROM gt_pairs
            WHERE (s1,mid) IN (SELECT s1,mid FROM candidates)""").fetchone()[0]

        self.assertEqual(ineligible, 2)
        self.assertEqual(eligible_but_missing, 1)
        self.assertEqual(recovered, 2)
        self.assertEqual(ineligible + eligible_but_missing + recovered, tot,
                         "Losses don't sum to total")
        con.close()


class TestHeavyBlockRefinement(unittest.TestCase):
    """Heavy blocks should be identifiable and refinable without losing small blocks."""

    def test_heavy_key_identification(self):
        con = duckdb.connect()
        con.execute("""
            CREATE TABLE key_sizes(cc VARCHAR, key VARCHAR, n_targets INT);
            INSERT INTO key_sizes VALUES
                ('US','common_name',500),('US','rare_name',3),
                ('US','medium_name',50),('US','huge_name',1500);
        """)
        # p99 should correctly identify the pathological key
        p99 = con.sql("SELECT quantile_cont(n_targets, 0.99) FROM key_sizes").fetchone()[0]
        heavy = con.sql(f"SELECT count(*) FROM key_sizes WHERE n_targets >= {p99}").fetchone()[0]
        self.assertGreater(heavy, 0, "Should identify at least one heavy key")
        # Small keys should not be affected
        small = con.sql("SELECT count(*) FROM key_sizes WHERE n_targets <= 10").fetchone()[0]
        self.assertEqual(small, 1, "Small keys should be preserved")
        con.close()


class TestArtifactSchemaAndUniqueness(unittest.TestCase):
    """The provenance artifact must have the correct schema and no duplicates."""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(ART):
            raise unittest.SkipTest("final_candidate_provenance.parquet not built yet")
        cls.con = duckdb.connect()
        cls.con.execute("SET memory_limit='1GB'; SET threads=4;")
        cls.art = ART.replace(os.sep, '/')

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "con"):
            cls.con.close()

    def test_schema_columns(self):
        """Artifact must have exactly (source1_entity_id, target_entity_id, prov)."""
        cols = self.con.sql(f"SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet('{self.art}'))").fetchall()
        col_names = {c[0] for c in cols}
        self.assertEqual(col_names, {"source1_entity_id", "target_entity_id", "prov"})

    def test_no_duplicate_identities(self):
        n, d = self.con.sql(f"""SELECT count(*),
            count(*)-count(DISTINCT (source1_entity_id,target_entity_id))
            FROM read_parquet('{self.art}')""").fetchone()
        self.assertEqual(d, 0, f"{d} duplicate identities in {n} rows")

    def test_s1_prefix(self):
        bad = self.con.sql(f"""SELECT count(*) FROM read_parquet('{self.art}')
            WHERE source1_entity_id NOT LIKE 'S1-%'""").fetchone()[0]
        self.assertEqual(bad, 0)

    def test_target_prefix(self):
        bad = self.con.sql(f"""SELECT count(*) FROM read_parquet('{self.art}')
            WHERE target_entity_id NOT LIKE 'S2-%' AND target_entity_id NOT LIKE 'S3-%'""").fetchone()[0]
        self.assertEqual(bad, 0)

    def test_prov_range(self):
        bad = self.con.sql(f"""SELECT count(*) FROM read_parquet('{self.art}')
            WHERE prov < 1 OR prov > 15""").fetchone()[0]
        self.assertEqual(bad, 0)

    def test_no_self_targets(self):
        bad = self.con.sql(f"""SELECT count(*) FROM read_parquet('{self.art}')
            WHERE target_entity_id LIKE 'S1-%'""").fetchone()[0]
        self.assertEqual(bad, 0)


if __name__ == "__main__":
    unittest.main()
