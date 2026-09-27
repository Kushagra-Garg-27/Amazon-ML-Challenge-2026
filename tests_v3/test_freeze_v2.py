"""Prepared evidence-gate tests; no assessment data is accessed."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/v3'))
from freeze_v2 import validate_evidence


class TestFreezeEvidence(unittest.TestCase):
    def test_reproduction_must_cover_scores_decisions_and_same_metric(self):
        assessment={'status':'COMPLETE_ONE_TIME_INTERNAL_ASSESSMENT',
                    'metrics':{'macro_f05':0.9},'policy_sha256_before_labels':'policy',
                    'model_artifact_sha256':'model'}
        reproduction={'status':'PASS','exact_score_array_equal':True,'exact_decisions_equal':True,
                      'exact_decision_scores_equal':True,'macro_f05':0.9}
        audit={'status':'PASS','decision_SHA':'policy','selected_model_SHA':'model'}
        validate_evidence(assessment,reproduction,audit,'policy','model')
        for patch in ({'exact_decision_scores_equal':False},{'macro_f05':0.89},{'status':'PENDING'},
                      {'macro_f05':float('nan')}):
            with self.subTest(patch=patch),self.assertRaises(PermissionError):
                validate_evidence(assessment,{**reproduction,**patch},audit,'policy','model')
        with self.assertRaises(PermissionError):
            validate_evidence(assessment,reproduction,audit,'changed-policy','model')
