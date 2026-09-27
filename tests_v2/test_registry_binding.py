"""Reject stale eligibility artifacts before closeout writes; synthetic IDs only."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.v2_close_gate import sha, validate_registry_proof
from er.candidates_v2.firewall import id_checksum


class RegistryBindingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / 'eligible.parquet'
        pq.write_table(pa.table({'source1_entity_id': pa.array([], type=pa.string())}), self.path)
        self.proof = {
            'status': 'blocked_insufficient_untouched_s1',
            'artifacts': {'eligible_s1': {'path': 'eligible.parquet', 'sha256': sha(self.path)}},
            'eligible_s1': 0, 'counts': {'eligible_old_model_fit_minus_touched_s1': 0},
            'old_model_fit_s1': 2, 'touched_model_fit_s1': 2,
            'eligible_id_sha256': id_checksum([]),
            'logical_checksums': {'eligible_s1': id_checksum([])},
        }

    def test_valid_registry_binding(self):
        validate_registry_proof(self.proof, self.path, [], self.root)

    def test_changed_file_is_rejected(self):
        pq.write_table(pa.table({'source1_entity_id': ['unrelated']}), self.path)
        with self.assertRaisesRegex(RuntimeError, 'binary_sha256'):
            validate_registry_proof(self.proof, self.path, [], self.root)

    def test_inconsistent_registry_fields_fail_closed(self):
        mutations = [
            lambda p: p.update(status='failed'),
            lambda p: p.update(eligible_s1=1),
            lambda p: p.update(touched_model_fit_s1=1),
            lambda p: p.update(eligible_id_sha256='wrong'),
            lambda p: p['counts'].update(eligible_old_model_fit_minus_touched_s1=1),
            lambda p: p['logical_checksums'].update(eligible_s1='wrong'),
            lambda p: p['artifacts']['eligible_s1'].update(path='other.parquet'),
        ]
        for index, mutate in enumerate(mutations):
            proof = deepcopy(self.proof)
            mutate(proof)
            with self.subTest(index=index), self.assertRaises(RuntimeError):
                validate_registry_proof(proof, self.path, [], self.root)

    def test_missing_registry_fields_fail_closed(self):
        with self.assertRaises(RuntimeError):
            validate_registry_proof({}, self.path, [], self.root)


if __name__ == '__main__':
    unittest.main()
