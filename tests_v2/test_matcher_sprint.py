import unittest
import numpy as np
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))

from er.matcher_v2.features import name_features,address_features,script_features,cross_features
from er.matcher_v2.decision import entity_f05,choose_expected_set,resolve_conflicts
from er.matcher_v2.access import assert_authorized,role_for
from er.matcher_v2.evaluate import evaluate


class TestMatcherSprint(unittest.TestCase):
    def test_names_and_empty(self):
        x=name_features('acme trading','acme trading')
        self.assertEqual(x['name_ng3'],1)
        self.assertEqual(name_features('','')['name_ng3'],0)
        self.assertEqual(x,name_features('acme trading','acme trading'))

    def test_script_local(self):
        self.assertGreater(script_features('भारत','bharat')['translit_ratio'],.7)
        self.assertEqual(script_features('','')['translit_ratio'],0)

    def test_address_missing_distinct_from_conflict(self):
        x=address_features('12 main road 110001','')
        self.assertEqual(x['addr_missing'],1)
        self.assertEqual(x['house_conflict_v2'],0)
        y=address_features('12 main road 110001','13 main road 110001')
        self.assertEqual(y['house_conflict_v2'],1)
        self.assertEqual(y['postal_equal_v2'],1)

    def test_cross_source_excludes_self_and_labels(self):
        target={'mid':'S2-a','name':'acme','addr':'12 main'}
        anchors=[dict(target,score=.99),{'mid':'S3-b','name':'acme','addr':'12 main','score':.9}]
        x=cross_features(target,anchors)
        self.assertEqual(x['cross_support_count'],1)
        self.assertEqual(x,cross_features(dict(target,y=1),[dict(a,y=0) for a in anchors]))

    def test_membership_fails_closed(self):
        assert_authorized(['a'],{'a'})
        with self.assertRaises(PermissionError):assert_authorized(['a','closed'],{'a'})
        self.assertEqual(role_for('a'),role_for('a'))

    def test_v1_change_guard_ignores_new_v2_files_only(self):
        from er.matcher_v2.access import v1_tracked_changes
        changes=['A\tscripts/v2_new.py','M\tscripts/existing_v1.py','D\tcode/business_entity_resolution/src/er/io.py']
        self.assertEqual(v1_tracked_changes(changes),changes[1:])

    def test_macro_includes_singletons_and_retrieval_misses(self):
        result=entity_f05(np.array([0,2,1]),np.array([0,1,1]),np.array([0,1,0]))
        np.testing.assert_allclose(result,[1,1.25/1.5,0])

    def test_low_confidence_can_be_empty(self):
        self.assertEqual(choose_expected_set(np.array([.01,.02])).sum(),0)
        self.assertEqual(choose_expected_set(np.array([.99,.98])).sum(),2)

    def test_target_conflict_tie_is_deterministic(self):
        ids=np.array([1,0,2]); target=np.array(['x','x','y']); score=np.array([.9,.9,.8])
        np.testing.assert_array_equal(resolve_conflicts(ids,target,score,np.ones(3,bool)),[False,True,True])

    def test_full_truth_recall_includes_absent_candidates(self):
        m,_=evaluate(np.array([0,1]),np.array([1,0]),np.array([.9,.01]),
            np.array([2,0,1]),np.array([0,1,2]))
        self.assertAlmostEqual(m['macro_f05'],(1.25/1.5+1)/3)
        self.assertAlmostEqual(m['candidate_pair_recall'],1/3)

    def test_feature_order_excludes_labels_and_identifiers(self):
        from er.matcher_v2.data import FEATURES
        self.assertEqual(len(FEATURES),len(set(FEATURES)))
        self.assertFalse({'label','y_train','entity_index','role','source1_entity_id','target_entity_id'}&set(FEATURES))

    def test_rates_include_single_target_and_no_match(self):
        m,_=evaluate(np.array([0,1]),np.array([0,1]),np.array([.9,.1]),np.array([0,1]),np.array([0,1]))
        self.assertEqual(m['underprediction_rate'],.5)
        self.assertEqual(m['overprediction_rate'],.5)

    def test_complexity_rule_rejects_small_gain(self):
        from er.matcher_v2.experiments import select_simple_model
        cheap={'feature_set':['a'],'metrics':{'macro_f05':.92},'runtime':1}
        expensive={'feature_set':['a','b'],'metrics':{'macro_f05':.9205},'runtime':10}
        self.assertIs(select_simple_model([cheap,expensive]),cheap)
        expensive['metrics']['macro_f05']=.925
        self.assertIs(select_simple_model([cheap,expensive]),expensive)

    def test_entity_weights_preserve_class_mass(self):
        from er.matcher_v2.experiments import entity_training_weights
        entity=np.array([0,0,0,1,1,1,1]);y=np.array([1,0,0,1,1,0,0]);truth=np.array([1,2])
        w=entity_training_weights(entity,y,truth)
        self.assertAlmostEqual(float(w[y==1].mean()),1.)
        self.assertAlmostEqual(float(w[y==0].mean()),1.)
        self.assertAlmostEqual(float(w[0]),float(w[3]+w[4]))

    def test_frozen_binding_rejects_modified_bytes_before_labels(self):
        import json,tempfile
        from pathlib import Path
        from er.matcher_v2.access import sha
        from er.matcher_v2.experiments import validate_selected_artifacts
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=root/'models/demo/model.txt';model.parent.mkdir(parents=True)
            model.write_text('original');spec=root/'feature_spec.json';spec.write_text('{}')
            selected=root/'selected_model.json'
            selected.write_text(json.dumps({'model':'LightGBM','experiment_id':'demo','artifact_SHA':sha(model)}))
            policy={'model_manifest_sha256':sha(selected),'feature_spec_sha256':sha(spec),'decision':{'kind':'global','threshold':.61}}
            (root/'decision_policy.json').write_text(json.dumps(policy))
            validate_selected_artifacts(root,root,True)
            spec.write_text('{"changed":true}')
            with self.assertRaises(PermissionError):validate_selected_artifacts(root,root,True)
            spec.write_text('{}');model.write_text('changed')
            with self.assertRaises(PermissionError):validate_selected_artifacts(root,root,True)

    def test_policy_pins_original_manifest_after_extension_appears(self):
        import json,tempfile
        from pathlib import Path
        from er.matcher_v2.experiments import selected_manifest_path
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'selected_model_extension.json').write_text('{}')
            (root/'decision_policy.json').write_text(json.dumps({'model_manifest_file':'selected_model.json'}))
            self.assertEqual(selected_manifest_path(root).name,'selected_model_extension.json')
            self.assertEqual(selected_manifest_path(root,True).name,'selected_model.json')

    def test_ensemble_hashes_cover_every_component(self):
        import json,tempfile
        from pathlib import Path
        from er.matcher_v2.access import sha
        from er.matcher_v2.experiments import validate_selected_artifacts
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=root/'models/blend/model.json';model.parent.mkdir(parents=True)
            part=root/'component.txt';part.write_text('native bytes')
            model.write_text(json.dumps({'components':[{'artifact':'component.txt','artifact_SHA':sha(part)}]}))
            (root/'selected_model.json').write_text(json.dumps({'model':'Ensemble','experiment_id':'blend','artifact_SHA':sha(model)}))
            validate_selected_artifacts(root,root)
            part.write_text('mutated')
            with self.assertRaises(PermissionError):validate_selected_artifacts(root,root)

if __name__=='__main__':unittest.main()
