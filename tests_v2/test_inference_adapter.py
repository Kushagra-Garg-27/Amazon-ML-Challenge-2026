import sys
import unittest
from pathlib import Path

import numpy as np
import pyarrow as pa

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))

from er.matcher_v2.data import V1,FEATURES


def candidate_table():
    ids=['S1-a','S1-a','S1-a','S1-b'];targets=['S2-x','S3-y','S3-z','S2-q']
    values={name:np.zeros(4,np.float32) for name in V1}
    values['target_is_s2']=np.array([1,0,0,1],np.float32)
    return pa.table({'source1_entity_id':ids,'target_entity_id':targets,**values})


class TestInferenceAdapter(unittest.TestCase):
    def setUp(self):
        self.sources={
          'S1-a':{'name_norm':'acme trading','addr_norm':'12 main','country_norm':'france'},
          'S1-b':{'name_norm':'zeta','addr_norm':'','country_norm':'france'}}
        self.targets={
          'S2-x':{'name_norm':'acme trading','addr_norm':'12 main','country_norm':'france'},
          'S3-y':{'name_norm':'acme trading','addr_norm':'12 main','country_norm':'france'},
          'S3-z':{'name_norm':'other','addr_norm':'99 side','country_norm':'france'},
          'S2-q':{'name_norm':'zeta','addr_norm':'','country_norm':'france'}}
        self.weights={'france':{'name':{'acme':2.,'trading':2.},'addr':{'12':2.,'main':2.}}}
        self.scores=np.array([.9,.8,.4,.3],np.float32)

    def test_exact_candidate_context_and_cross_source_anchors(self):
        from er.matcher_v2.inference import adapt_table
        out=adapt_table(candidate_table(),self.sources,self.targets,self.weights,self.scores)
        self.assertEqual(out.column_names,['source1_entity_id','target_entity_id',*FEATURES])
        np.testing.assert_allclose(out['candidate_count'],[3,3,3,1])
        np.testing.assert_allclose(out['source_candidate_count'],[1,2,2,1])
        np.testing.assert_allclose(out['top_score'],[.9,.8,.8,.3])
        np.testing.assert_allclose(out['second_score'],[0,.4,.4,0])
        np.testing.assert_allclose(out['score_gap'],[.9,.4,.4,.3])
        np.testing.assert_allclose(out['high_count'],[1,1,1,0])
        np.testing.assert_allclose(out['relative_to_top'],[0,0,-.4,0],atol=1e-7)
        self.assertAlmostEqual(out['cross_name'][0].as_py(),1.)
        self.assertAlmostEqual(out['cross_seed_score'][0].as_py(),.8)
        self.assertAlmostEqual(out['cross_seed_score'][1].as_py(),.9)
        self.assertEqual(out['name_weighted_jaccard'][0].as_py(),1.)

    def test_label_columns_do_not_change_features(self):
        from er.matcher_v2.inference import adapt_table
        base=candidate_table();labeled=base.append_column('label',pa.array([1,0,0,1]))
        a=adapt_table(base,self.sources,self.targets,self.weights,self.scores)
        b=adapt_table(labeled,self.sources,self.targets,self.weights,self.scores)
        self.assertTrue(a.equals(b))

    def test_duplicate_pairs_and_missing_records_fail_closed(self):
        from er.matcher_v2.inference import adapt_table
        base=candidate_table()
        duplicate=pa.concat_tables([base,base.slice(0,1)])
        with self.assertRaises(ValueError):
            adapt_table(duplicate,self.sources,self.targets,self.weights,np.r_[self.scores,.2])
        sources=dict(self.sources);del sources['S1-b']
        with self.assertRaises(KeyError):
            adapt_table(base,sources,self.targets,self.weights,self.scores)

    def test_frozen_teacher_scores_reproduce_materialized_scores(self):
        import pyarrow.parquet as pq
        from er.matcher_v2.inference import score_v1_table
        source=ROOT/'work/v2_matcher_sprint_r1/features/v1_part_00_india.parquet'
        table=pq.read_table(source,columns=['source1_entity_id','target_entity_id',*V1,'v1_score']).slice(0,64)
        actual=score_v1_table(table,ROOT/'work/final_matcher_model.txt',
          ROOT/'work/final_matcher_policy.json','76ff78a7cc97e40174b6d631a6eaa062c786f0480c6693fd6387e90be6761d21')
        np.testing.assert_array_equal(actual,np.asarray(table['v1_score'],dtype=np.float32))
        with self.assertRaises(PermissionError):
            score_v1_table(table,ROOT/'work/final_matcher_model.txt',
              ROOT/'work/final_matcher_policy.json','0'*64)


if __name__=='__main__':unittest.main()
