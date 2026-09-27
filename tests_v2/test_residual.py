import unittest
import numpy as np
import tempfile
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.matcher_v2.residual import context,logits

class TestResidual(unittest.TestCase):
    def test_context_uses_scores_and_sources_only(self):
        d={'entity':np.array([0,0,1]),'source':np.array([True,False,True]),'y':np.array([1,0,1])}
        p=np.array([.9,.8,.3],np.float32);a=context(d,p);d['y']=1-d['y']
        np.testing.assert_array_equal(a,context(d,p))
        np.testing.assert_allclose(a[:,1],[.9,.9,.3])
        np.testing.assert_allclose(a[:,7],p)
        self.assertTrue(np.isfinite(logits(np.array([0.,1.]))).all())

    def test_saved_residual_reproduces_training_validation_predictions(self):
        import lightgbm as lgb
        from er.matcher_v2.data import FEATURES
        from er.matcher_v2.residual import predict,EXTRA
        rng=np.random.default_rng(42);x=rng.random((40,len(FEATURES)),dtype=np.float32)
        p=np.linspace(.1,.9,40,dtype=np.float32);y=(x[:,0]>.5).astype(float)
        d={'X':x,'entity':np.arange(40),'source':np.arange(40)%2==0}
        full=np.column_stack((x,context(d,p)));record=[]
        dt=lgb.Dataset(full,label=y,init_score=logits(p),feature_name=FEATURES+EXTRA)
        def capture(pred,data):record.append(pred.copy());return 'dummy',0.,True
        model=lgb.train({'objective':'binary','metric':'None','num_threads':1,'num_leaves':3,
          'min_data_in_leaf':2,'verbosity':-1},dt,num_boost_round=4,valid_sets=[dt],feval=capture)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'model.txt';model.save_model(str(path))
            np.testing.assert_allclose(predict(d,p,path),record[-1],atol=1e-7)

    def test_residual_policy_exports_actual_decision_scores(self):
        from unittest.mock import patch
        from er.matcher_v2.policies import apply_policy
        corrected=np.array([.8,.2],np.float32);raw=np.array([.3,.9],np.float32)
        with patch('er.matcher_v2.policies.sha',return_value='expected'),patch('er.matcher_v2.residual.predict',return_value=corrected):
            accepted,decision=apply_policy({},raw,{'kind':'residual','artifact':'dummy','artifact_SHA':'expected','threshold':.5},return_scores=True)
        np.testing.assert_array_equal(accepted,[True,False])
        np.testing.assert_array_equal(decision,corrected)

if __name__=='__main__':unittest.main()
