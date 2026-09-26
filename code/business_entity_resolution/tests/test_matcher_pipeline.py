"""Deterministic matcher split, feature, sampling, and evaluation tests."""
from pathlib import Path
import json,math,sys,tempfile,unittest
import duckdb

ROOT=Path(__file__).resolve().parents[3]; sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.evaluate import f_beta_entity
from er.features.exact import exact_features,script_class
from er.features.fuzzy import fuzzy_features
from er.features.schema import FEATURES,FEATURE_NAMES,validate_spec
from er.features.token import token_features
from er.matcher.baseline import metrics
from er.matcher.sampling import deterministic_key

def rec(name,address,country='france'):
 toks=' '.join(sorted(set(name.split())))
 return {'name_norm':name,'name_nosuffix':name.replace(' ltd',''),'name_sorted':toks,'addr_norm':address,'country_norm':country}

class TestFeatureFixture(unittest.TestCase):
 def test_schema_unique_and_label_free(self):
  validate_spec(); self.assertEqual(len(FEATURES),65); self.assertNotIn('y',FEATURE_NAMES); self.assertEqual(len(FEATURE_NAMES),len(set(FEATURE_NAMES)))
 def test_exact_and_reordered_name(self):
  a=rec('acme bakery','12 main street 75001'); b=rec('bakery acme','12 main street 75001')
  e=exact_features(a,b); t=token_features(a,b); self.assertFalse(e['exact_name_norm']); self.assertTrue(e['exact_name_sorted']); self.assertEqual(t['name_jaccard'],1)
 def test_suffix_only_difference(self):
  a=rec('acme ltd','12 main'); b=rec('acme','12 main'); self.assertTrue(exact_features(a,b)['exact_name_nosuffix'])
 def test_typo_and_abbreviation_fuzzy(self):
  a=rec('international bakery','12 avenue'); b=rec('internatonal bkry','12 ave'); f=fuzzy_features(a,b); self.assertGreater(f['name_ratio'],.7); self.assertGreater(f['address_ratio'],.7)
 def test_missing_and_conflicting_numbers(self):
  a=rec('a','12 main 75001'); b=rec('a','99 main 75001'); e=exact_features(a,b); self.assertTrue(e['conflicting_address_numbers']); self.assertTrue(e['exact_postal_token']); self.assertFalse(e['house_number_equal'])
  b=rec('a',''); self.assertTrue(exact_features(a,b)['target_address_missing'])
 def test_cross_script_and_unseen_country(self):
  a=rec('नमस्ते दुकान','1 road'); b=rec('namaste shop','1 road'); e=exact_features(a,b); self.assertTrue(e['script_conflict']); self.assertEqual(script_class(a['name_norm']),2); self.assertTrue(e['country_agreement'])
 def test_no_nan_inf(self):
  a=rec('','','france'); out={**exact_features(a,a),**token_features(a,a),**fuzzy_features(a,a)}
  self.assertTrue(all(not isinstance(v,float) or math.isfinite(v) for v in out.values()))
 def test_deterministic_feature_order(self): self.assertEqual(FEATURE_NAMES,tuple(x['name'] for x in FEATURES))

class TestSplitsSamplingAndEvaluation(unittest.TestCase):
 def test_split_counts_overlap_and_checksums(self):
  p=ROOT/'work/matcher_split_checksums.json'; self.assertTrue(p.exists()); d=json.loads(p.read_text()); self.assertEqual(d['s1_overlap'],0); self.assertEqual(d['labelled_target_overlap'],0); self.assertEqual(sum(d['counts'].values()),2_206_821); self.assertEqual(len({x['entity_id_sha256'] for x in d['splits'].values()}),4)
 def test_final_eval_firewall(self):
  self.assertFalse(any((ROOT/'work').glob('*final_eval*feature*'))); self.assertFalse(any((ROOT/'work').glob('*final_eval*score*')))
 def test_sampling_key_deterministic(self): self.assertEqual(deterministic_key('S2-1'),deterministic_key('S2-1')); self.assertNotEqual(deterministic_key('S2-1'),deterministic_key('S3-1'))
 def test_all_positive_retention_and_source_balance(self):
  c=duckdb.connect(); full=c.sql("select sum(y) from read_parquet('work/feature_pilot_labels.parquet') l join read_parquet('work/feature_pilot_s1.parquet') p on l.source1_entity_id=p.entity_id where p.split='model_train'").fetchone()[0]
  for p in (ROOT/'work/negative_samples').glob('*.parquet'): self.assertEqual(c.sql(f"select sum(y) from read_parquet('{p.as_posix()}')").fetchone()[0],full)
  row=c.sql("select count(*) filter(where target_is_s2),count(*) filter(where target_is_s3) from read_parquet('work/negative_samples/hybrid_source_balanced.parquet') where y=0").fetchone(); self.assertLess(abs(row[0]-row[1]),1000); c.close()
 def test_identity_invariant_and_restart(self):
  d=json.loads((ROOT/'work/feature_pilot_audit.json').read_text()); self.assertTrue(d['equal']); self.assertEqual((d['added'],d['removed'],d['duplicates']),(0,0,0))
 def test_full_calibration_scored_and_subset(self):
  c=duckdb.connect(); n=c.sql("select count(*) from read_parquet('work/feature_pilot_v1_final/model_calibration_*.parquet')").fetchone()[0]; s=c.sql("select count(*) from read_parquet('work/baseline_calibration_scores.parquet')").fetchone()[0]; self.assertEqual(n,s)
  bad=c.sql("select count(*) from read_parquet('work/baseline_calibration_scores.parquet') s anti join read_parquet('work/feature_pilot_candidates/model_calibration_*.parquet') c using(source1_entity_id,target_entity_id)").fetchone()[0]; self.assertEqual(bad,0); c.close()
 def test_multimatch_singleton_and_oracle(self):
  ids=['a','a','b']; mids=['x','y','z']; scores=[.9,.8,.1]; truth={'a':{'x','y'},'b':set()}; country={'a':'france','b':'france'}
  out,pred=metrics(ids,mids,scores,truth,.5,country,{}); self.assertEqual(pred['a'],{'x','y'}); self.assertFalse(pred['b']); self.assertEqual(out['macro_f0_5'],1.0); self.assertEqual(out['singleton_accuracy'],1.0)
 def test_candidate_and_matcher_error_separate(self):
  truth={'a','b','c'}; candidates={'a','b'}; predicted={'a'}; self.assertEqual(truth-candidates,{'c'}); self.assertEqual((truth&candidates)-predicted,{'b'}); self.assertFalse((truth-candidates)&((truth&candidates)-predicted))

if __name__=='__main__': unittest.main()
