"""Small direct-join tests for the materialized candidate freeze gate."""
from pathlib import Path
import sys
import unittest

import duckdb

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "business_entity_resolution" / "src"))
from er.candidates.policies import DYNAMIC_K_SQL
from er.candidates.ranking import EVIDENCE_ORDER_SQL, HEAVY_ORDER_SQL


class TestLossPopulations(unittest.TestCase):
    def test_membership_is_direct_join_and_exclusive(self):
        c = duckdb.connect()
        c.execute("""CREATE TABLE gt(s1 VARCHAR,mid VARCHAR,eligible BOOL);
            INSERT INTO gt VALUES ('1','a',true),('1','b',true),('1','c',false),('2','d',false);
            CREATE TABLE selected(s1 VARCHAR,mid VARCHAR);
            INSERT INTO selected VALUES ('1','a');""")
        ineligible = set(c.sql("""SELECT g.s1,g.mid FROM gt g ANTI JOIN selected s USING(s1,mid)
            WHERE NOT eligible""").fetchall())
        cap = set(c.sql("""SELECT g.s1,g.mid FROM gt g ANTI JOIN selected s USING(s1,mid)
            WHERE eligible""").fetchall())
        self.assertEqual(ineligible, {("1", "c"), ("2", "d")})
        self.assertEqual(cap, {("1", "b")})
        self.assertFalse(ineligible & cap)
        self.assertEqual(len(ineligible | cap), 3)
        c.close()

    def test_real_population_counts_and_membership(self):
        out = ROOT / "work" / "freeze_gate"
        if not (out / "cap_ranking_loss.parquet").exists():
            self.skipTest("audit artifacts absent")
        c = duckdb.connect()
        key = (out / "key_ineligible_loss.parquet").as_posix()
        cap = (out / "cap_ranking_loss.parquet").as_posix()
        oracle = (out / "oracle.parquet").as_posix()
        artifact = (ROOT / "work" / "final_candidate_provenance.parquet").as_posix()
        self.assertEqual(c.sql(f"SELECT count(*) FROM read_parquet('{key}')").fetchone()[0], 43959)
        self.assertEqual(c.sql(f"SELECT count(*) FROM read_parquet('{cap}')").fetchone()[0], 61897)
        bad = c.sql(f"""SELECT count(*) FROM read_parquet('{key}') WHERE eligible""").fetchone()[0]
        self.assertEqual(bad, 0)
        bad = c.sql(f"""SELECT count(*) FROM read_parquet('{cap}') WHERE NOT eligible""").fetchone()[0]
        self.assertEqual(bad, 0)
        overlap = c.sql(f"""SELECT count(*) FROM read_parquet('{key}') k
            JOIN read_parquet('{cap}') p USING(s1,mid)""").fetchone()[0]
        self.assertEqual(overlap, 0)
        selected = c.sql(f"""SELECT count(*) FROM read_parquet('{oracle}') o JOIN
            read_parquet('{artifact}') a ON o.s1=a.source1_entity_id AND o.mid=a.target_entity_id""").fetchone()[0]
        self.assertEqual(selected + 43959 + 61897, 763919)
        c.close()


class TestPolicyAccounting(unittest.TestCase):
    def test_source_quotas_and_gain_loss(self):
        c = duckdb.connect()
        c.execute("""CREATE TABLE ranked(s1 VARCHAR,mid VARCHAR,rsource INT);
            INSERT INTO ranked VALUES ('1','S2-a',1),('1','S2-b',2),('1','S2-c',3),
              ('1','S3-a',1),('1','S3-b',2),('1','S3-c',3);
            CREATE TABLE gt(s1 VARCHAR,mid VARCHAR);
            INSERT INTO gt VALUES ('1','S2-a'),('1','S2-c'),('1','S3-b');
            CREATE TABLE baseline(s1 VARCHAR,mid VARCHAR);
            INSERT INTO baseline VALUES ('1','S2-a'),('1','S2-c');""")
        c.execute("""CREATE TABLE selected AS SELECT s1,mid FROM ranked WHERE rsource<=2""")
        quotas = dict(c.sql("SELECT substr(mid,1,2),count(*) FROM selected GROUP BY 1").fetchall())
        self.assertEqual(quotas, {"S2": 2, "S3": 2})
        c.execute("CREATE TABLE hits AS SELECT g.* FROM gt g JOIN selected s USING(s1,mid)")
        gained = c.sql("SELECT mid FROM hits ANTI JOIN baseline USING(s1,mid)").fetchall()
        lost = c.sql("SELECT mid FROM baseline ANTI JOIN hits USING(s1,mid)").fetchall()
        self.assertEqual(gained, [("S3-b",)])
        self.assertEqual(lost, [("S2-c",)])
        self.assertEqual(len(gained) - len(lost), 0)
        c.close()


class TestDeterministicRanking(unittest.TestCase):
    def rank_evidence(self):
        c = duckdb.connect()
        c.execute("""CREATE TABLE ev(s1 VARCHAR,mid VARCHAR,both_pass BOOL,postal_shared BOOL,
            numeric_shared BOOL,exact_name BOOL,exact_addr BOOL,score DOUBLE,sh INT,
            coverage_s1 DOUBLE,coverage_target DOUBLE,jaccard DOUBLE);
            INSERT INTO ev VALUES
            ('1','S2-b',true,false,false,false,false,1,1,.5,.5,.3),
            ('1','S2-a',true,false,false,false,false,1,1,.5,.5,.3),
            ('1','S3-z',false,true,true,true,true,9,4,1,1,1),
            ('1','S2-c',true,true,false,false,false,.5,1,.5,.5,.3);""")
        rows = c.sql(f"SELECT mid FROM ev ORDER BY {EVIDENCE_ORDER_SQL}").fetchall()
        c.close()
        return [r[0] for r in rows]

    def test_evidence_beats_raw_idf_and_breaks_id_ties(self):
        self.assertEqual(self.rank_evidence(), ["S2-c", "S2-a", "S2-b", "S3-z"])

    def test_restart_reproduces_order(self):
        self.assertEqual(self.rank_evidence(), self.rank_evidence())

    def test_dynamic_bands_and_hard_bound(self):
        c = duckdb.connect()
        c.execute("""CREATE TABLE ev(s1 VARCHAR,sort_n INT,addr_n INT,both_n INT);
            INSERT INTO ev VALUES ('strong',100,1,1),('medium_addr',100,1,0),
              ('medium_sorted',5,0,0),('weak',100,0,0),('none',0,0,0);""")
        rows = dict(c.sql(f"""SELECT s1,{DYNAMIC_K_SQL} FROM ev e
            LEFT JOIN (SELECT s1,both_n n FROM ev) b USING(s1)
            LEFT JOIN (SELECT s1,addr_n n FROM ev) a USING(s1)
            LEFT JOIN (SELECT s1,sort_n n FROM ev) n USING(s1)""").fetchall())
        self.assertEqual(rows, {"strong": 25, "medium_addr": 50,
                                "medium_sorted": 50, "weak": 75, "none": 75})
        self.assertLessEqual(max(rows.values()), 75)
        c.close()

    def test_heavy_secondary_evidence_and_cap(self):
        c = duckdb.connect()
        c.execute("""CREATE TABLE heavy(s1 VARCHAR,mid VARCHAR,exact_addr BOOL,
            postal_shared BOOL,numeric_shared BOOL,exact_name BOOL,addr_jaccard DOUBLE);
            INSERT INTO heavy VALUES ('1','S2-z',false,false,false,true,.1),
              ('1','S2-a',true,false,false,false,.8),
              ('1','S3-b',false,true,true,false,.9);""")
        ranked = c.sql(f"""SELECT mid,row_number() OVER (PARTITION BY s1
            ORDER BY {HEAVY_ORDER_SQL}) rk FROM heavy ORDER BY rk""").fetchall()
        self.assertEqual(ranked, [("S2-a", 1), ("S3-b", 2), ("S2-z", 3)])
        self.assertEqual([mid for mid,rk in ranked if rk<=2], ["S2-a", "S3-b"])
        c.close()

    def test_heavy_cap_preserves_unranked_sorted_pairs(self):
        c = duckdb.connect()
        c.execute("""CREATE TABLE selected(s1 VARCHAR,mid VARCHAR,prov INT);
            INSERT INTO selected VALUES ('small','S2-1',1),('heavy','S2-2',1),
                ('heavy','S2-3',1),('heavy','S2-4',5);
            CREATE TABLE ranked(s1 VARCHAR,mid VARCHAR,rk INT);
            INSERT INTO ranked VALUES ('heavy','S2-2',1),('heavy','S2-3',2),
                ('heavy','S2-4',3);""")
        kept = set(c.sql("""SELECT a.s1,a.mid FROM selected a LEFT JOIN ranked h USING(s1,mid)
            WHERE h.rk IS NULL OR NOT (a.prov=1 AND h.rk>1)""").fetchall())
        self.assertEqual(kept, {("small", "S2-1"), ("heavy", "S2-2"), ("heavy", "S2-4")})
        c.close()


if __name__ == "__main__":
    unittest.main()
