"""Allocate prospectively sealed V2 populations after the approved amendment."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code/business_entity_resolution/src'))
from er.candidates_v2.firewall import id_checksum, REQUESTED
from er.candidates_v2.prospective import assign, SEED
from v2_preserve import sha

AUTHORIZATION = Path('C:/Users/kusha/.codex/attachments/a4288409-6071-42b5-a277-01c337f20d46/Pasted text.txt')


def event(kind, **data):
    with (ROOT / 'work/v2_access_ledger.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps({'event_id': kind + ':' + datetime.now(timezone.utc).isoformat(),
                            'recorded_utc': datetime.now(timezone.utc).isoformat(),
                            'operation': kind, 'v2_research_label_read': False,
                            'sealed_label_read': False, **data}, sort_keys=True) + '\n')


def run():
    work = ROOT / 'work'
    eligible_path = work / 'v2_eligible_s1_v2.parquet'
    table = pq.read_table(eligible_path)
    if table.num_columns != 1:
        raise RuntimeError('Eligible pool must contain only S1 IDs')
    ids = table.column(0).to_pylist()
    if len(ids) != len(set(ids)):
        raise RuntimeError('Duplicate eligible S1')
    classification = work / 'v2_historical_touch_reclassification.json'
    if not classification.exists():
        raise RuntimeError('Reclassification proof missing')
    proof = json.loads(classification.read_text(encoding='utf-8'))
    if proof.get('status') != 'ELIGIBLE_POOL_SUFFICIENT' or proof.get('reclassified_source_count') != 598:
        raise RuntimeError('Reclassification gate is incomplete')
    expected_eligible = proof['artifacts']['eligible_s1']
    if (expected_eligible['path'] != eligible_path.relative_to(ROOT).as_posix()
            or expected_eligible['sha256'] != sha(eligible_path)
            or proof['eligible_s1'] != len(ids)
            or proof['eligible_id_sha256'] != id_checksum(ids)):
        raise RuntimeError('Reclassified eligible IDs differ from the proof')
    rows = assign(ids)
    touched_path = work / 'v2_decision_relevant_touched_s1.parquet'
    expected_touched = proof['artifacts']['touched_s1']
    if (expected_touched['path'] != touched_path.relative_to(ROOT).as_posix()
            or expected_touched['sha256'] != sha(touched_path)):
        raise RuntimeError('C/D touched membership file changed')
    touched_table = pq.read_table(touched_path)
    touched = set(touched_table.column(0).to_pylist())
    if len(touched) != proof['decision_relevant_touched_s1']:
        raise RuntimeError('C/D touched membership count changed')
    if set(ids) & touched:
        raise RuntimeError('C/D membership entered eligible pool')
    if len(rows) != len(ids) or len({e for e,_ in rows}) != len(ids):
        raise RuntimeError('Split assignment lost or duplicated an eligible ID')
    prior = work / 'v2_rule1_archive'
    prior.mkdir(exist_ok=True)
    for name in ('v2_split_manifest.parquet', 'v2_split_checksums.json', 'v2_split_report.md', 'v2_test_results.json'):
        old = work / name
        backup = prior / name
        if old.exists() and not backup.exists():
            shutil.copyfile(old, backup)
    dest = work / 'v2_prospective_sealed'
    dest.mkdir(exist_ok=True)
    schema = pa.schema([('entity_id', pa.string()), ('population', pa.string())],
                       metadata={b'v2_state': b'PROSPECTIVELY_SEALED', b'rule': b'human_amendment_1'})
    tmp = work / 'v2_split_manifest.pending.parquet'
    pq.write_table(pa.Table.from_pylist([dict(entity_id=e, population=p) for e,p in rows], schema=schema),tmp,compression='zstd')
    out = work / 'v2_split_manifest.parquet'
    previous = json.loads((work / 'v2_split_checksums.json').read_text())
    if previous.get('status') == 'ALLOCATED_PROSPECTIVE_SEAL':
        if sha(tmp) != sha(out):
            raise RuntimeError('Refusing to resize/reassign a previously sealed manifest')
    os.replace(tmp,out)
    populations = {}
    for name in [*REQUESTED, 'v2_matcher_train']:
        members = [e for e,p in rows if p == name]
        path = (work if name == 'v2_candidate_research' else dest) / (name + '.parquet')
        pq.write_table(pa.table({'entity_id': members}),path,compression='zstd')
        populations[name] = {'path': path.relative_to(ROOT).as_posix(), 's1': len(members),
                             'id_sha256': id_checksum(members), 'sha256': sha(path),
                             'label_access': 'NOT_OPENED'}
    result = {'status': 'ALLOCATED_PROSPECTIVE_SEAL', 'rule': 'human_amendment_1',
              'seed': SEED, 'ordering': 'sha256(seed + NUL + S1 UTF-8) binary ascending, S1 ascending',
              'allocation_performed': True, 'eligible_s1': len(ids), 'eligible_id_sha256': id_checksum(ids),
              'eligible_file_sha256': sha(eligible_path), 's1_overlap': 0, 'overlap_with_C_D': 0,
              'manifest': {'path':out.relative_to(ROOT).as_posix(), 'sha256':sha(out)},
              'reclassification_sha256':sha(classification), 'populations':populations,
              'authorization': {'path':str(AUTHORIZATION),'sha256':sha(AUTHORIZATION)},
              'terminology':'prospectively sealed v2 populations',
              'historical_exposure':'Population aggregate scans and integrity checks disclosed; C/D decision-relevant membership excluded',
              'target_uniqueness': {'path':'work/integrity_report.md', 'sha256':sha(work/'integrity_report.md'),
                                    'direct_sealed_target_overlap':'deferred; no sealed labels opened'}}
    (work/'v2_split_checksums.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    report = ['# V2 prospectively sealed populations', '',
              'The previous stop was correct under the original historical-use rule. The human amendment permits disclosed aggregate-only scans and ID-only membership use. The original registry and stop report remain unchanged.', '',
              'Their per-S1 labels, outcomes and metrics were not previously exposed or used for decision-relevant candidate/model development, based on the reconstructed historical evidence, and they are prospectively sealed from this point forward.', '',
              'Known historical exposure includes full model_train link-count, singleton, mean-link and match-count summaries, full-file scans and global integrity/target-uniqueness checks. We do not claim that these S1s were never computationally scanned or included in aggregates.', '',
              '| Population | S1 | Logical ID SHA-256 |', '|---|---:|---|']
    report += [f"| {n} | {p['s1']:,} | `{p['id_sha256']}` |" for n,p in populations.items()]
    report += ['', f'Manifest SHA-256: `{sha(out)}`.', f'Seed: `{SEED}`. SHA-256 binary order then S1 ID tie break. No C/D overlap; no duplicate S1 assignments.', '',
               'Only candidate_research may be joined to labels, after a complete label-free candidate artifact is checksummed and audited. All other populations remain sealed. Direct target-overlap checks remain deferred; the existing global target-uniqueness invariant is referenced by checksum.', '']
    (work/'v2_split_report.md').write_text('\n'.join(report),encoding='utf-8')
    event('human_approved_eligibility_amendment',old_rule='Any historical labelled scan excluded the S1',
          prior_stop='All 1655792 old model_fit S1 included in broad aggregate audit; eligible0',
          new_rule='A MEMBERSHIP_ONLY / B AGGREGATE_ONLY permit; C DECISION_RELEVANT_ROW_LEVEL / D AMBIGUOUS exclude',
          human_authorization=result['authorization'],reclassification_sha256=sha(classification),
          eligible_s1=len(ids),known_aggregate_exposure=result['historical_exposure'],
          command='.venv\\Scripts\\python.exe -B scripts\\v2_allocate.py',populations=populations)
    print(json.dumps({'eligible_s1':len(ids),'populations':{n:p['s1'] for n,p in populations.items()},'manifest_sha256':sha(out)},indent=2))


if __name__=='__main__':
    run()
