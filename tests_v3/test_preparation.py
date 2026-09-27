"""Synthetic preparation checks; deliberately not run during capacity pause."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/v3'))
from package_release import safe_relative, validate_names, validate_test_receipt
from ngram_preflight import grams_sql

class TestPreparation(unittest.TestCase):
    def test_archive_rejects_unsafe_or_work_files(self):
        for name in ('../x','/x','C:/x','output/../x','code/business_entity_resolution/.env',
          'code/business_entity_resolution/work/labels.json','code/business_entity_resolution/features.parquet','dataset/train.tsv'):
            with self.assertRaises(ValueError):safe_relative(name)

    def test_archive_accepts_required_files(self):
        for name in ('output/matching_results.tsv','Documentation_template.md','code/business_entity_resolution/src/er/run.py'):
            self.assertEqual(str(safe_relative(name)),name)

    def test_windows_archive_aliases_are_rejected(self):
        for suffix in ('src/./run.py','src//run.py','src/run.py.', 'src/run.py ',
          'src/CON.txt','src/Lpt1.py','src/file:stream','src/a?.py','src/a\x00.py'):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                safe_relative('code/business_entity_resolution/'+suffix)
        with self.assertRaises(ValueError):
            validate_names(['code/business_entity_resolution/src/A.py',
                            'code/business_entity_resolution/src/a.py'])
        with self.assertRaises(ValueError):
            validate_names(['code/business_entity_resolution/src',
                            'code/business_entity_resolution/src/a.py'])

    def test_test_receipt_must_cover_packaged_code_at_same_commit(self):
        entry={'source':'code/run.py','archive_path':'code/business_entity_resolution/src/run.py','sha256':'a'*64}
        receipt={'status':'PASS','code_commit':'commit','source_files':[{'path':'code/run.py','sha256':'a'*64}],
                 'commands':[{'command':['python','-m','unittest'],'exit_code':0}], 'tests_run':1}
        validate_test_receipt(receipt,[entry],'commit')
        for changed in ({'code_commit':'old'}, {'source_files':[]}, {'tests_run':0},
                        {'commands':[{'command':['python'],'exit_code':1}]}):
            with self.subTest(changed=changed), self.assertRaises((ValueError,PermissionError)):
                validate_test_receipt({**receipt,**changed},[entry],'commit')

    def test_ngram_sql_parameters_are_closed(self):
        self.assertIn('substr(text_value,i,5)',grams_sql('text_value',5))
        with self.assertRaises(ValueError):grams_sql('label',4)
        with self.assertRaises(ValueError):grams_sql('text_value',2)
