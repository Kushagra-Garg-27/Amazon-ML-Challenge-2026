import unittest
from unittest.mock import patch
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))

class TestV3Firewall(unittest.TestCase):
    def test_retired_population_reader_fails_before_any_file_access(self):
        from er.candidates_v2.runtime import populations
        with patch('pathlib.Path.read_text',side_effect=AssertionError('file read')),patch('pyarrow.parquet.read_table',side_effect=AssertionError('parquet decode')):
            with self.assertRaises(PermissionError):populations()

if __name__=='__main__':unittest.main()
