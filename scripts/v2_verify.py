"""Run V1 compatibility tests within the V2 access boundary, plus V2 gate tests.

Four existing assertions are explicitly skipped because they decode real labels.
This is never reported as a complete V1 suite pass. V1 test files are unchanged.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

from v2_close_gate import record

ROOT = Path(__file__).resolve().parents[1]
BLOCKED = {
    'test_refinement.TestSelectedArtifactRecallByDirectGTJoin.test_recall_by_direct_join':
        'Raw train ground truth is closed during the V2 eligibility gate.',
    'test_freeze_gate.TestLossPopulations.test_real_population_counts_and_membership':
        'Historical row-level oracle/GT values are not reopened.',
    'test_matcher_pipeline.TestSplitsSamplingAndEvaluation.test_all_positive_retention_and_source_balance':
        'Historical pilot row labels are not reopened.',
    'test_controlled_matcher.TestFrozenDevelopmentArtifacts.test_all_positive_retention_hard_mining_and_source_balance':
        'Historical development row labels are not reopened.',
}


class BoundarySkip(unittest.TestCase):
    def __init__(self, identity, reason):
        super().__init__('runTest')
        self.identity, self.reason = identity, reason

    def id(self):
        return self.identity

    def __str__(self):
        return self.identity

    def runTest(self):
        self.skipTest(self.reason)


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def run_suite(tests, log_path):
    with log_path.open('w', encoding='utf-8') as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.TestSuite(tests))
    return {
        'discovered': len(tests), 'run_including_skips': result.testsRun,
        'executed': result.testsRun - len(result.skipped),
        'passed': result.testsRun - len(result.skipped) - len(result.failures) - len(result.errors),
        'failed': len(result.failures), 'errors': len(result.errors),
        'skipped': [{'test': t.id(), 'reason': reason} for t, reason in result.skipped],
        'exit_code': 0 if result.wasSuccessful() else 1,
        'log': log_path.relative_to(ROOT).as_posix(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--result-path', default='work/v2_test_results.json')
    args = parser.parse_args()
    result_path = ROOT / args.result_path
    command = '.venv\\Scripts\\python.exe -B scripts\\v2_verify.py'
    if args.result_path != 'work/v2_test_results.json':
        command += ' --result-path ' + args.result_path
    if result_path.exists() and args.result_path != 'work/v2_test_results.json':
        raise FileExistsError(f'Verification result already exists: {result_path}')
    os.chdir(ROOT)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['PYTHONUTF8'] = '1'
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'code/business_entity_resolution/src'))
    run_root = ROOT / 'work/v2_verification' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run_root.mkdir(parents=True, exist_ok=False)
    temporary = run_root / 'tmp'
    temporary.mkdir()
    record('v2-test-start-' + run_root.name, 'Run access-reviewed V1 assertions and synthetic V2 gate tests',
           ['scripts/v2_verify.py', run_root.relative_to(ROOT).as_posix()],
           command=command,
           historical_label_tests_skipped=list(BLOCKED), synthetic_fixture_labels_read=True,
           existing_historical_summary_reports_read=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(temporary)
    tempfile.tempdir = str(temporary)
    import duckdb
    original_connect = duckdb.connect

    def isolated_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        spill = temporary / ('duckdb_' + uuid.uuid4().hex)
        spill.mkdir()
        connection.execute("SET temp_directory='" + spill.as_posix().replace("'", "''") + "'")
        connection.execute("SET memory_limit='512MB'; SET threads=1")
        return connection

    duckdb.connect = isolated_connect
    discovered = list(flatten(unittest.TestLoader().discover(str(ROOT / 'code/business_entity_resolution/tests'))))
    if len(discovered) != 132 or not set(BLOCKED).issubset({test.id() for test in discovered}):
        details = {'count': len(discovered), 'missing_blocked': sorted(set(BLOCKED) - {test.id() for test in discovered}),
                   'import_errors': [repr(test._exception) for test in discovered if hasattr(test, '_exception')]}
        record('v2-test-discovery-failed-' + run_root.name, 'Stopped before any test execution',
               [run_root.relative_to(ROOT).as_posix()], details=details, exit_code=1)
        raise RuntimeError('V1 suite discovery mismatch; no tests executed: ' + json.dumps(details))
    v1 = [BoundarySkip(test.id(), BLOCKED[test.id()]) if test.id() in BLOCKED else test for test in discovered]
    result = {'command': command,
              'v1': run_suite(v1, run_root / 'v1_unittest.log')}
    v2 = list(flatten(unittest.TestLoader().discover(str(ROOT / 'tests_v2'))))
    result['v2'] = run_suite(v2, run_root / 'v2_unittest.log')
    result['complete_v1_suite_executed'] = False
    result['scope'] = ('All 132 V1 tests discovered; four label-reading tests replaced by explicit skips. '
                       'Frozen-artifact binary integrity checks permitted. Synthetic labels used only by fixture tests. '
                       'No V2 research labels opened by these tests, and no V2 matcher trained.')
    result['exit_code'] = max(result['v1']['exit_code'], result['v2']['exit_code'])
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    record('v2-test-complete-' + run_root.name, 'Record verification results',
           [result_path.relative_to(ROOT).as_posix()], v1_executed=result['v1']['executed'],
           v1_skipped=len(result['v1']['skipped']), v2_executed=result['v2']['executed'],
           exit_code=result['exit_code'], synthetic_fixture_labels_read=True)
    print(json.dumps(result, indent=2))
    return result['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
