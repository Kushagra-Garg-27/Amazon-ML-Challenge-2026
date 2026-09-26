"""Deterministic tests for the controlled matcher-development phase."""
from pathlib import Path
import json
import hashlib
import sys
import tempfile
import unittest

import lightgbm as lgb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import duckdb

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))

from er.features.schema import FEATURE_NAMES
from er.matcher.controlled import (
    MODEL_FEATURE_NAMES_V1_1, REMOVED_REDUNDANT_FEATURES, Population,
    assemble_predictions, evaluate, threshold_search, validate_model_feature_order,
    train_model, policy_mask, source_threshold_gap,
)


class TestFeatureV11(unittest.TestCase):
    def test_redundant_features_removed_without_mutating_v1(self):
        self.assertEqual(len(FEATURE_NAMES),65)
        self.assertEqual(set(REMOVED_REDUNDANT_FEATURES),{
            'retrieved_sorted_name','retrieved_exact_address','shared_postal_tokens','target_is_s3'})
        self.assertEqual(len(MODEL_FEATURE_NAMES_V1_1),61)
        self.assertFalse(set(REMOVED_REDUNDANT_FEATURES)&set(MODEL_FEATURE_NAMES_V1_1))
        self.assertEqual(tuple(x for x in FEATURE_NAMES if x not in REMOVED_REDUNDANT_FEATURES),MODEL_FEATURE_NAMES_V1_1)

    def test_model_feature_order_validation(self):
        self.assertTrue(validate_model_feature_order(list(MODEL_FEATURE_NAMES_V1_1),MODEL_FEATURE_NAMES_V1_1))
        with self.assertRaises(ValueError):
            validate_model_feature_order(list(reversed(MODEL_FEATURE_NAMES_V1_1)),MODEL_FEATURE_NAMES_V1_1)

    def test_dataset_constructed_with_training_seed_parameters(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'tiny.parquet'
            data={name:np.array([0,1,0,1],dtype=np.float32) for name in MODEL_FEATURE_NAMES_V1_1}
            data['source1_entity_id']=['a','b','c','d']; data['target_entity_id']=['x','y','z','q']
            data['y']=np.array([0,1,0,1],dtype=np.uint8)
            pq.write_table(pa.table(data),path)
            model,meta=train_model(path,MODEL_FEATURE_NAMES_V1_1,
                {'learning_rate':.1,'num_leaves':2,'min_data_in_leaf':1,'data_random_seed':42},2)
            self.assertEqual(model.num_feature(),61); self.assertEqual(meta['rows'],4)


class TestThresholdAndAssembly(unittest.TestCase):
    @staticmethod
    def population():
        return Population(
          scores=np.array([.9,.8,.4,.2],np.float32),entity=np.array([0,0,1,2],np.int32),
          y=np.array([1,1,0,0],np.uint8),s2=np.array([1,0,1,0],bool),
          address_missing=np.array([0,0,1,0],bool),script_conflict=np.zeros(4,bool),
          numeric_conflict=np.array([0,1,0,0],bool),
          truth_n=np.array([2,0,0],np.int16),recovered_n=np.array([2,0,0],np.int32),
          countries=np.array(['india','us','us']),entity_ids=('a','b','c'),
          truth_breakdown={'s2':1,'s3':1,'address_missing':0,'address_present':2,
                           'script_conflict':0,'script_same_or_empty':2})

    def test_threshold_grid_deterministic_and_empty_sets_allowed(self):
        first,_=threshold_search(self.population()); second,_=threshold_search(self.population())
        self.assertEqual(first,second)
        self.assertGreater(first['empty_prediction_rate'],0)

    def test_multi_match_singleton_empty_and_subset(self):
        ids=['a','a','b','c']; targets=['x','y','z','q']; scores=np.array([.9,.8,.4,.2])
        pred=assemble_predictions(ids,targets,scores,.5)
        self.assertEqual(pred,{'a':('x','y')})
        candidate_pairs=set(zip(ids,targets)); predicted={(s,t) for s,ts in pred.items() for t in ts}
        self.assertTrue(predicted<=candidate_pairs)
        self.assertNotIn('b',pred); self.assertNotIn('c',pred)

    def test_conflict_filter_and_source_thresholds_are_deterministic(self):
        pop=self.population()
        self.assertEqual(policy_mask(pop,.5,reject_numeric_conflict=False).tolist(),[True,True,False,False])
        self.assertEqual(policy_mask(pop,.5,reject_numeric_conflict=True).tolist(),[True,False,False,False])
        self.assertEqual(policy_mask(pop,.5,source_thresholds=(.95,.75)).tolist(),[False,True,False,False])
        self.assertEqual(source_threshold_gap(pop),source_threshold_gap(pop))


class TestFirewall(unittest.TestCase):
    def test_development_artifacts_exclude_final_eval(self):
        forbidden=[]
        for p in (ROOT/'work').rglob('*'):
            if p.is_file() and 'final_eval' in p.name.lower() and any(x in p.name.lower() for x in ('feature','label','score','prediction')):
                forbidden.append(p.name)
        self.assertEqual(forbidden,[])
        checks=json.loads((ROOT/'work/model_development_split_checksums.json').read_text())
        self.assertEqual(checks['s1_overlap'],0); self.assertEqual(checks['target_id_overlap'],0)
        self.assertEqual(checks['prior_pilot_not_fit'],0)


class TestFrozenDevelopmentArtifacts(unittest.TestCase):
    def test_ablation_selection_and_required_views(self):
        x=json.loads((ROOT/'work/model_development_results.json').read_text())
        self.assertEqual(x['ablations']['selected'],'all_v1')
        self.assertEqual(set(x['ablations']['experiments']),{
          'exact_provenance','exact_token','exact_token_numeric_address','all_non_fuzzy',
          'all_v1','without_rank_provenance','without_address','without_rapidfuzz'})

    def test_all_positive_retention_hard_mining_and_source_balance(self):
        c=duckdb.connect()
        expected=c.sql("""select sum(l.y) from read_parquet('work/model_development_labels.parquet') l
          join read_parquet('work/model_development_samples.parquet') s on l.source1_entity_id=s.entity_id
          where s.tier_b""").fetchone()[0]
        for name in ('current_hybrid','mixed','mined'):
            path=ROOT/f'work/model_development_negative_samples/{name}_tier_b.parquet'
            self.assertEqual(c.sql(f"select sum(y) from read_parquet('{path.as_posix()}')").fetchone()[0],expected)
        s2,s3=c.sql("""select count(*) filter(where y=0 and target_is_s2),count(*) filter(where y=0 and target_is_s3)
          from read_parquet('work/model_development_negative_samples/mined_tier_b.parquet')""").fetchone()
        self.assertLess(abs(s2-s3),1000)
        bad=c.sql("""select count(*) from read_parquet('work/model_development_negative_samples/mined_tier_b.parquet') m
          anti join read_parquet('work/scores_mining_fit/*.parquet') s using(source1_entity_id,target_entity_id)""").fetchone()[0]
        self.assertEqual(bad,0); c.close()
        results=json.loads((ROOT/'work/model_development_results.json').read_text())
        self.assertEqual(results['negative_sampling']['selected'],'mined')

    def test_native_model_reload_feature_order_and_score_reproducibility(self):
        model=lgb.Booster(model_file=(ROOT/'work/final_matcher_model.txt').as_posix())
        self.assertEqual(tuple(model.feature_name()),MODEL_FEATURE_NAMES_V1_1)
        repro=json.loads((ROOT/'work/matcher_reproducibility.json').read_text())
        self.assertTrue(repro['feature_checksum_equal'])
        self.assertEqual(repro['max_absolute_score_difference'],0.0)
        self.assertTrue(repro['identical_threshold_decisions'])
        self.assertTrue(repro['identical_prediction_sets'])
        self.assertTrue(repro['predictions_subset_of_candidates'])

    def test_candidate_and_matcher_errors_are_separate(self):
        x=json.loads((ROOT/'work/final_matcher_error_analysis.json').read_text())
        for pop in ('baseline_dev','model_tune'):
            self.assertGreater(x[pop]['candidate_generation_loss'],0)
            self.assertGreater(x[pop]['matcher_false_negative'],0)
            self.assertNotEqual(x[pop]['candidate_generation_loss'],x[pop]['matcher_false_negative'])

    def test_final_manifest_checksums_and_firewall(self):
        manifest=json.loads((ROOT/'work/final_matcher_manifest.json').read_text())
        self.assertFalse(manifest['model_final_eval_accessed'])
        self.assertFalse(manifest['grouped_stress_evaluated'])
        self.assertFalse(manifest['test_inference_run'])
        for item in manifest['artifacts']:
            p=ROOT/item['path']; self.assertTrue(p.exists())
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),item['sha256'])
        policy=json.loads((ROOT/'work/final_matcher_policy.json').read_text())
        self.assertEqual(policy['threshold'],.61)
        self.assertTrue(policy['set_assembly']['allow_empty'])
        self.assertTrue(policy['set_assembly']['allow_multiple'])
        self.assertFalse(policy['set_assembly']['force_top_1'])


if __name__=='__main__': unittest.main()
