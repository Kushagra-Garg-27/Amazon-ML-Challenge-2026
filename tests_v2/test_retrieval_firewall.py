"""Synthetic full-target retrieval fixture; no historical or sealed labels."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates import pilot


class FullTargetCorpusFixture(unittest.TestCase):
    def test_research_s1_queries_target_universe_without_gt_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            (base/'work/keys').mkdir(parents=True)
            (base/'work/freeze_gate').mkdir(parents=True)
            def row(entity,name):
                return {'entity_id':entity,'country_norm':'india','name_norm':name,
                        'name_nosuffix':name,'name_sorted':name,'addr_norm':'',
                        'num_tokens':''}
            pq.write_table(pa.Table.from_pylist([row('S1-research','acme')]),base/'work/keys/train_s1.parquet')
            pq.write_table(pa.Table.from_pylist([row('S2-sealed-linked','acme')]),base/'work/keys/train_s2.parquet')
            pq.write_table(pa.Table.from_pylist([row('S3-other','other')]),base/'work/keys/train_s3.parquet')
            pq.write_table(pa.table({'cc':['india'],'tok':['acme'],'df':[1]}),base/'work/freeze_gate/df_name.parquet')
            pq.write_table(pa.table({'cc':['india'],'tok':['road'],'df':[1]}),base/'work/freeze_gate/df_addr.parquet')
            selection=base/'selection.parquet'
            pq.write_table(pa.table({'entity_id':['S1-research'],'split':['research'],
                                     'country_norm':['india']}),selection)
            previous=Path.cwd()
            try:
                os.chdir(base)
                output=base/'v2_candidates.parquet'
                pilot.materialize_group(selection,'research','india',output)
                con=duckdb.connect()
                pairs=con.execute('SELECT source1_entity_id,target_entity_id FROM read_parquet(?)',
                                  [output.as_posix()]).fetchall()
                con.close()
            finally:
                os.chdir(previous)
            self.assertIn(('S1-research','S2-sealed-linked'),pairs)
            self.assertFalse((base/'ground_truth.tsv').exists())


if __name__=='__main__': unittest.main()
