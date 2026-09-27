"""Synthetic amended eligibility and prospective label-scope tests."""
import hashlib
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code/business_entity_resolution/src'))
sys.path.insert(0, str(ROOT))
from er.candidates_v2.prospective import (
    assign, assert_research_only, excludes, scoped_gt_rows,
)
from er.candidates_v2.ngram import grams
from scripts.v2_reclassify import classify_source


class ExposureTaxonomy(unittest.TestCase):
    def test_aggregate_audit_is_disclosed_without_exclusion(self):
        source = {'source_id': 'executed_matcher_labelled_audit:model_train',
                  'artifact_path': 'work/matcher_split_manifest.parquet',
                  'distinct_s1_count': 3}
        result = classify_source(source)
        self.assertEqual(result['exposure_category'], 'B')
        self.assertEqual(result['excluded_s1_count'], 0)
        self.assertFalse(excludes(result['exposure_category_name']))

    def test_row_level_candidate_and_grouped_val_exclude(self):
        for source in (
            {'source_id': 'artifact:work/model_development_features/example.parquet',
             'artifact_path': 'work/model_development_features/example.parquet', 'distinct_s1_count': 2},
            {'source_id': 'artifact:work/split_s1_grouped.parquet',
             'artifact_path': 'work/split_s1_grouped.parquet', 'distinct_s1_count': 3},
        ):
            with self.subTest(source=source['source_id']):
                result=classify_source(source)
                self.assertEqual(result['exposure_category'], 'C')
                self.assertEqual(result['excluded_s1_count'], source['distinct_s1_count'])

    def test_ambiguous_fails_closed(self):
        result=classify_source({'source_id':'unresolved_process',
                                'artifact_path':'work/unresolved.parquet','distinct_s1_count':5})
        self.assertEqual(result['exposure_category'],'D')
        self.assertEqual(result['excluded_s1_count'],5)
        self.assertTrue(excludes('AMBIGUOUS'))
        with self.assertRaises(ValueError): excludes('UNKNOWN')


class ProspectiveSeal(unittest.TestCase):
    def test_stable_assignment_counts_and_fresh_process(self):
        ids=[f'id{i:06d}' for i in range(400011)]
        rows=assign(ids)
        self.assertEqual(len(rows),len(ids))
        self.assertEqual(len(set(x for x,_ in rows)),len(ids))
        from collections import Counter
        counts=Counter(p for _,p in rows)
        self.assertEqual(counts['v2_candidate_research'],100000)
        self.assertEqual(counts['v2_candidate_holdout'],100000)
        self.assertEqual(counts['v2_matcher_tune'],50000)
        self.assertEqual(counts['v2_threshold'],50000)
        self.assertEqual(counts['v2_final_eval'],100000)
        self.assertEqual(counts['v2_matcher_train'],11)
        code="from er.candidates_v2.prospective import assign; print(assign(['a']+[f'id{i:06d}' for i in range(400000)])[0])"
        env={**__import__('os').environ,'PYTHONPATH':str(ROOT/'code/business_entity_resolution/src'),'PYTHONDONTWRITEBYTECODE':'1'}
        a=subprocess.check_output([sys.executable,'-B','-c',code],env=env)
        b=subprocess.check_output([sys.executable,'-B','-c',code],env=env)
        self.assertEqual(a,b)

    def test_no_sealed_id_in_label_join(self):
        with self.assertRaises(PermissionError):
            assert_research_only(['hold'], {'research'}, {'hold'})
        with self.assertRaises(PermissionError):
            assert_research_only(['research'], {'research'}, {'research'})

    def test_binary_filter_does_not_decode_sealed_target_bytes(self):
        lines=[b'source1_entity_id\tmatched_entity_ids\n',
               b'sealed\t\xff\xff\n', b'research\tS2-1,S3-1\n']
        self.assertEqual(list(scoped_gt_rows(lines,{'research'},{'sealed'})),
                         [('research',('S2-1','S3-1'))])

    def test_character_gram_lengths_dedup_and_unicode(self):
        self.assertEqual(grams('aaaa',2),('aa',))
        self.assertEqual(grams('amazon',3),('ama','azo','maz','zon'))
        self.assertEqual(len(grams('नमस्ते',4)),3)
        self.assertEqual(grams('abc',4),())
        self.assertEqual(grams('a'*65,4),())


if __name__=='__main__': unittest.main()
