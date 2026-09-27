import unittest
import numpy as np
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from sklearn.ensemble import RandomForestRegressor
from er.matcher_v2.entity_utility import forest_artifact,predict_forest,summary_features

class TestEntityUtility(unittest.TestCase):
    def test_utility_retains_unretrieved_truth_and_empty_entity(self):
        from er.matcher_v2.policies import utility_targets
        d={'entity':np.array([0,1]),'y':np.array([1,0]),'truth':np.r_[2,0,np.zeros(99998,dtype=int)]}
        values=utility_targets(d,np.array([.9,.1]),[.5,1.01])
        np.testing.assert_allclose(values[:2],[[1.25/1.5,0],[1,1]])

    def test_conflict_validation_excludes_calibration_fit_entities(self):
        import pyarrow as pa
        from er.matcher_v2.policies import apply_policy
        d={'entity':np.array([0,1]),'meta':pa.table({'target_entity_id':['same','same']})}
        policy={'kind':'global','threshold':.5,'conflicts':True}
        np.testing.assert_array_equal(apply_policy(d,np.array([.9,.8]),policy,conflict_active=np.array([1])),[False,True])
        np.testing.assert_array_equal(apply_policy(d,np.array([.9,.8]),policy),[True,False])

    def test_portable_forest_matches_sklearn(self):
        rng=np.random.default_rng(42);x=rng.normal(size=(100,4)).astype(np.float32)
        y=np.column_stack((x[:,0]>.2,x[:,1]<.3)).astype(float)
        model=RandomForestRegressor(n_estimators=4,max_depth=3,random_state=42).fit(x,y)
        np.testing.assert_allclose(model.predict(x),predict_forest(forest_artifact(model),x),atol=1e-12)

    def test_entity_summaries_label_free_and_empty_safe(self):
        from er.matcher_v2.data import FEATURES
        x=np.zeros((3,len(FEATURES)),np.float32)
        d={'X':x,'entity':np.array([0,0,2]),'source':np.array([True,False,True]),
           'address':np.array([False,True,False]),'y':np.array([1,0,0])}
        a=summary_features(d,np.array([.8,.6,.2]),size=4)
        d['y']=1-d['y'];b=summary_features(d,np.array([.8,.6,.2]),size=4)
        np.testing.assert_array_equal(a,b)
        self.assertTrue(np.isfinite(a).all());self.assertEqual(a[0,0],.8)
        self.assertEqual(a[1,0],0)

if __name__=='__main__':unittest.main()
