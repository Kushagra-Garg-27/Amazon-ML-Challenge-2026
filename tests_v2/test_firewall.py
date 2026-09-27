"""Synthetic safety tests; no dataset or label-bearing artifact is opened."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code/business_entity_resolution/src'))
from er.candidates_v2.firewall import (
    REQUESTED, allocation_gate, canonical_ids, eligible_ids, id_checksum,
    require_research_access,
)


class EligibilityTests(unittest.TestCase):
    def test_model_fit_membership_alone_is_not_exposure(self):
        self.assertEqual(eligible_ids(['fit-a', 'fit-b'], []), ('fit-a', 'fit-b'))

    def test_excludes_historically_audited_subset(self):
        self.assertEqual(eligible_ids(['a', 'b', 'c'], ['b', 'outside']), ('a', 'c'))

    def test_executed_full_scope_can_exhaust_source_pool(self):
        self.assertEqual(eligible_ids(['a', 'b'], ['a', 'b', 'tune']), ())

    def test_duplicate_sources_do_not_change_difference(self):
        self.assertEqual(eligible_ids(['a', 'a', 'b'], ['a', 'a']), ('b',))

    def test_logical_checksum_is_stable_for_set_order(self):
        self.assertEqual(id_checksum(['b', 'a', 'a']), hashlib.sha256(b'a\nb\n').hexdigest())

    def test_invalid_ids_fail_closed(self):
        for ids in ([''], [None], ['a\nb'], ['a\rb']):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                canonical_ids(ids)

    def test_fresh_process_checksum_determinism(self):
        code = "from er.candidates_v2.firewall import id_checksum; print(id_checksum(['b','a','a']))"
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'code/business_entity_resolution/src'), PYTHONDONTWRITEBYTECODE='1')
        first = subprocess.check_output([sys.executable, '-B', '-c', code], env=env)
        second = subprocess.check_output([sys.executable, '-B', '-c', code], env=env)
        self.assertEqual(first, second)


class AllocationGateTests(unittest.TestCase):
    def test_zero_pool_records_exact_shortage_without_allocating(self):
        gate = allocation_gate(0)
        self.assertEqual(gate['shortage_s1'], 400_000)
        self.assertEqual(gate['requested_counts'], REQUESTED)
        self.assertIsNone(gate['actual_counts'])
        self.assertFalse(gate['allocation_performed'])

    def test_no_silent_resize(self):
        gate = allocation_gate(399_999)
        self.assertEqual(gate['shortage_s1'], 1)
        self.assertEqual(sum(gate['requested_counts'].values()), 400_000)

    def test_capacity_does_not_authorize_labels(self):
        gate = allocation_gate(400_000)
        self.assertEqual(gate['status'], 'READY_FOR_SPLIT')
        self.assertFalse(gate['label_access_authorized'])
        with self.assertRaises(PermissionError):
            require_research_access(gate)

    def test_blocked_gate_never_calls_label_loader(self):
        calls = []
        with self.assertRaises(PermissionError):
            require_research_access(allocation_gate(0))
            calls.append('would read labels')
        self.assertFalse(calls)

    def test_forged_gate_cannot_open_labels(self):
        with self.assertRaises(PermissionError):
            require_research_access({'status': 'PASS', 'label_access_authorized': True})

    def test_bad_counts_rejected(self):
        for count in (-1, 1.5, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                allocation_gate(count)

    def test_real_manifest_is_explicitly_blocked_or_prospectively_sealed(self):
        import pyarrow.parquet as pq
        report = json.loads((ROOT / 'work/v2_split_checksums.json').read_text())
        table = pq.read_table(ROOT / 'work/v2_split_manifest.parquet')
        if report['status'] == 'BLOCKED_INSUFFICIENT_POOL':
            self.assertEqual(report['eligible_s1'], 0)
            self.assertFalse(report['allocation_performed'])
            self.assertEqual(table.num_rows, 0)
            self.assertEqual(table.schema.metadata[b'v2_state'], b'BLOCKED_NO_ALLOCATION')
        elif report['status'] == 'ALLOCATED_PROSPECTIVE_SEAL':
            self.assertEqual(table.num_rows, report['eligible_s1'])
            self.assertEqual(table.schema.metadata[b'v2_state'], b'PROSPECTIVELY_SEALED')
            self.assertEqual(report['s1_overlap'], 0)
            self.assertEqual(report['overlap_with_C_D'], 0)
        else:
            self.fail('Unexpected split status')


if __name__ == '__main__':
    unittest.main()
