"""Tiny exact-audit fixtures. Prepared, NOT executed during the capacity pause."""
import sys
from pathlib import Path
import unittest
import duckdb
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/v3'))
from audit_outputs import audit_tables


class TestOutputAudit(unittest.TestCase):
    def setUp(self):
        self.c=duckdb.connect()
        self.addCleanup(self.c.close)
        for table in ('universe','target_ids'):
            self.c.execute(f'CREATE TABLE {table}(entity_id VARCHAR)')
        self.c.execute("INSERT INTO universe VALUES ('S1-a'),('S1-b')")
        self.c.execute("INSERT INTO target_ids VALUES ('S2-a'),('S3-b')")
        self.c.execute('CREATE TABLE candidate(source1_entity_id VARCHAR,candidate_entity_ids VARCHAR)')
        self.c.execute('CREATE TABLE matching(source1_entity_id VARCHAR,matched_entity_ids VARCHAR)')
        self.c.execute("INSERT INTO candidate VALUES ('S1-a','S2-a,S3-b'),('S1-b','')")
        self.c.execute("INSERT INTO matching VALUES ('S1-a','S2-a'),('S1-b','')")
        self.c.execute('CREATE TABLE actual_candidates(source1_entity_id VARCHAR,target_entity_id VARCHAR)')
        self.c.execute("INSERT INTO actual_candidates VALUES ('S1-a','S2-a'),('S1-a','S3-b')")
        self.c.execute('CREATE TABLE actual_scores(source1_entity_id VARCHAR,target_entity_id VARCHAR,accepted BOOLEAN,decision_score DOUBLE)')
        self.c.execute("INSERT INTO actual_scores VALUES ('S1-a','S2-a',true,0.9),('S1-a','S3-b',false,0.1)")

    def audit(self):
        return audit_tables(self.c,expected_count=2)

    def test_complete_input_with_empty_s1_passes(self):
        result=self.audit()
        self.assertEqual(result['candidate_pair_count'],2)
        self.assertEqual(result['accepted_match_count'],1)
        self.assertEqual(result['empty_prediction_count'],1)

    def test_bad_rows_are_rejected_before_partitioning(self):
        mutations=(
            "INSERT INTO actual_candidates VALUES (NULL,'S2-a')",
            "INSERT INTO actual_scores VALUES (NULL,'S2-a',true,0.9)",
            "INSERT INTO actual_scores VALUES ('S1-unknown','S2-a',true,0.9)",
            "UPDATE actual_scores SET target_entity_id=NULL WHERE accepted",
            "UPDATE actual_scores SET accepted=NULL WHERE accepted",
            "UPDATE actual_scores SET decision_score='NaN'::DOUBLE WHERE accepted",
            "UPDATE actual_scores SET decision_score=NULL WHERE accepted",
            "INSERT INTO target_ids VALUES (NULL)",
            "INSERT INTO target_ids VALUES ('S2-a')",
        )
        for sql in mutations:
            with self.subTest(sql=sql):
                self.c.execute('BEGIN')
                try:
                    self.c.execute(sql)
                    with self.assertRaises(RuntimeError):self.audit()
                finally:self.c.execute('ROLLBACK')

    def test_pair_identity_and_coverage_failures(self):
        mutations=(
            "INSERT INTO actual_candidates VALUES ('S1-a','S2-a')",
            "DELETE FROM actual_scores WHERE NOT accepted",
            "UPDATE actual_scores SET accepted=true WHERE NOT accepted",
            "UPDATE matching SET matched_entity_ids='S2-unknown' WHERE source1_entity_id='S1-a'",
            "UPDATE candidate SET candidate_entity_ids='S2-a,S2-a' WHERE source1_entity_id='S1-a'",
            "DELETE FROM matching WHERE source1_entity_id='S1-b'",
        )
        for sql in mutations:
            with self.subTest(sql=sql):
                self.c.execute('BEGIN')
                try:
                    self.c.execute(sql)
                    with self.assertRaises(RuntimeError):self.audit()
                finally:self.c.execute('ROLLBACK')

    def test_accepted_must_be_boolean(self):
        self.c.execute('ALTER TABLE actual_scores ALTER accepted TYPE VARCHAR')
        with self.assertRaises(RuntimeError):self.audit()
