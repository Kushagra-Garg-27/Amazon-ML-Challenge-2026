"""Read-only verification of frozen V1 identifiers and the release tag."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    'work/final_candidate_policy.json': '46fd324b3d4db7dbdf5fa4b8ce528f681335ce16f759fb93a39ba5b98a040fea',
    'work/feature_spec_v1_1.json': '37982fd6377aa2e36fe1c5dec0484a35c02ba4bcc4e3b67b3a337b33597f6e1f',
    'work/final_matcher_model.txt': '76ff78a7cc97e40174b6d631a6eaa062c786f0480c6693fd6387e90be6761d21',
    'work/final_matcher_policy.json': 'ced4ee4133110f5f55c184c3d4020b5087ecac5398bdf9ebfd03f76b3662aba3',
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 << 20), b''):
            h.update(block)
    return h.hexdigest()


def run():
    artifacts = {name: {'expected': expected, 'actual': sha(ROOT / name)}
                 for name, expected in EXPECTED.items()}
    tag = subprocess.check_output(['git', 'rev-parse', 'release_v1'],
                                  cwd=ROOT, text=True).strip()
    tracked = subprocess.check_output(['git', 'diff', '--name-only', 'release_v1', '--',
        'code/business_entity_resolution/src/er', 'scripts'], cwd=ROOT, text=True).strip()
    passed = all(x['expected'] == x['actual'] for x in artifacts.values()) and \
        tag == 'f6917432f267f8b36aefb4f392e2040069377dc2' and not tracked
    result = {'status': 'PASS' if passed else 'FAIL',
              'scope': 'frozen V1 identifiers and tracked release source; no test rows or output files read',
              'artifacts': artifacts, 'release_v1_tag_commit': tag,
              'tracked_release_source_diff': tracked.splitlines(),
              'historical_full_immutability_manifest': 'work/v2_v1_immutability_manifest.json'}
    path = ROOT / 'work/v2_phase2_r1/v1_integrity_check.json'
    path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'tag': tag,
        'frozen_ids_checked': len(artifacts), 'tracked_diff_count': len(result['tracked_release_source_diff'])}))
    if not passed:
        raise RuntimeError('Frozen V1 integrity verification failed')


if __name__ == '__main__':
    run()
