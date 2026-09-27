"""Independent consistency checks for the completed Phase 2 receipts and report."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'work/v2_phase2_r1'


def load(name):
    return json.loads((OUT / name).read_text(encoding='utf-8'))


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            h.update(block)
    return h.hexdigest()


def run():
    step, expansion, cap, resources, inv, tests = (load(x) for x in (
        'step0_evaluation.json','expansion_evaluation.json','cap_evaluation.json',
        'resource_projection_with_generation.json','artifact_inventory.json',
        'test_results_final.json'))
    assert len(expansion['configurations']) == 18
    assert len(cap['configurations']) == 6
    assert step['v1']['s1'] == step['v1_plus_all']['s1'] == 100000
    assert step['v1_plus_all']['macro_f05']-step['v1']['macro_f05'] > 0.0065
    assert step['paired_bootstrap']['ci_95'][0] > 0
    assert cap['remaining_gt_misses_after_plus_all'] == 345150-310916
    for name, x in expansion['configurations'].items():
        m = x['primary_frozen_v1']
        assert x['genuinely_new_candidates'] + x['already_in_plus_all'] == x['expansion_candidates']
        assert x['union_candidate_count'] == 18895613+x['genuinely_new_candidates']
        assert m['predicted']-step['v1_plus_all']['predicted'] == \
               m['final_true_positives_introduced']+m['final_false_positives_introduced']
        assert m['true_positive']-step['v1_plus_all']['true_positive'] == m['final_true_positives_introduced']
        assert resources['policies'][name]['projected_test_candidates'] == \
               round(x['union_candidate_count']*1732544/100000)
    for name, x in cap['configurations'].items():
        m = x['frozen_matcher']
        assert x['union_candidate_count'] == 18895613+x['added_candidates']
        assert m['predicted']-step['v1_plus_all']['predicted'] == \
               m['final_true_positives_introduced']+m['final_false_positives_introduced']
        assert resources['policies'][name]['projected_test_candidates'] == \
               round(x['union_candidate_count']*1732544/100000)
    for item in inv['files']:
        assert sha(ROOT / item['path']) == item['sha256'], item['path']
    assert load('v1_integrity_check.json')['status'] == 'PASS'
    assert tests['v1']['passed'] == 128 and len(tests['v1']['skipped']) == 4
    assert tests['v2']['passed'] == 26 and tests['exit_code'] == 0
    report = (OUT / 'report.md').read_text(encoding='utf-8')
    tail = report.split('## Final six answers\n\n',1)[1].strip().splitlines()
    assert len(tail) == 6 and all(x.startswith(f'{i}. ') for i,x in enumerate(tail,1))
    assert 'sealed-population row-level membership IDs were decoded once' in report
    plan = (ROOT / 'v2_strategic_research_plan.md').read_text(encoding='utf-8')
    assert 'PHASE 2 MEASURED ADDENDUM' in plan
    assert 'CANNOT bridge' not in plan and 'mathematically unreachable' not in plan
    print(json.dumps({'status':'PASS','grid_configs':18,'cap_configs':6,
        'inventory_files_checked':len(inv['files']),'final_answers':6,
        'v1_tests_passed':128,'v1_tests_skipped':4,'v2_tests_passed':26,
        'report_sha256':sha(OUT/'report.md')}))


if __name__ == '__main__':
    run()
