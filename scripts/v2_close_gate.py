"""Record an insufficient-pool stop; never allocate a smaller population."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code/business_entity_resolution/src'))
from er.candidates_v2.firewall import allocation_gate, id_checksum, PROTECTION_ORDER


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            h.update(block)
    return h.hexdigest()


def validate_registry_proof(proof: dict, eligible_path: Path, eligible: list[str], root: Path = ROOT) -> None:
    """Bind a closeout to the completed registry run before writing any marker."""
    try:
        entry = proof['artifacts']['eligible_s1']
        expected = {
            'status': proof['status'] == 'blocked_insufficient_untouched_s1',
            'path': entry['path'] == eligible_path.resolve().relative_to(root.resolve()).as_posix(),
            'binary_sha256': entry['sha256'] == sha(eligible_path),
            'eligible_count': proof['eligible_s1'] == len(eligible),
            'difference_count': proof['counts']['eligible_old_model_fit_minus_touched_s1'] == len(eligible),
            'source_accounting': proof['old_model_fit_s1'] == proof['touched_model_fit_s1'] + len(eligible),
            'logical_sha256': proof['eligible_id_sha256'] == id_checksum(eligible),
            'logical_checksum_copy': proof['logical_checksums']['eligible_s1'] == id_checksum(eligible),
        }
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError('Incomplete or invalid historical registry proof') from error
    failed = [name for name, matches in expected.items() if not matches]
    if failed:
        raise RuntimeError('Historical registry proof mismatch: ' + ', '.join(failed))


def record(event_id: str, operation: str, paths: list[str], **details) -> None:
    ledger = ROOT / 'work/v2_access_ledger.jsonl'
    events = [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
    if any(event.get('event_id') == event_id for event in events):
        return
    event = {
        'event_id': event_id, 'recorded_utc': datetime.now(timezone.utc).isoformat(),
        'operation': operation, 'paths': paths, 'real_row_label_values_read': False,
        'v2_research_label_read': False, 'sealed_label_read': False,
        'raw_dataset_access': False, **details,
    }
    with ledger.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(event, sort_keys=True) + '\n')


def main() -> None:
    eligible_path = ROOT / 'work/v2_eligible_s1.parquet'
    eligible = pq.read_table(eligible_path, columns=['source1_entity_id'])['source1_entity_id'].to_pylist()
    if len(eligible) != len(set(eligible)):
        raise RuntimeError('Duplicate eligible IDs; firewall cannot be certified')
    gate = allocation_gate(len(eligible))
    if gate['status'] != 'BLOCKED_INSUFFICIENT_POOL':
        raise RuntimeError('This closeout script handles only section 2A; no allocation or label permission granted')
    registry = ROOT / 'work/v2_historical_touch_checksums.json'
    if not registry.exists():
        raise RuntimeError('Historical registry proof is required first')
    proof = json.loads(registry.read_text(encoding='utf-8'))
    validate_registry_proof(proof, eligible_path, eligible)
    invariant = ROOT / 'work/integrity_report.md'
    invariant_sha = 'a15c836cc7feb7891d3a34f0faf28d11072feea1fb91b02be44871584051f8e8'
    if sha(invariant) != invariant_sha:
        raise RuntimeError('Historical target-uniqueness evidence changed')
    schema = pa.schema([('entity_id', pa.string()), ('population', pa.string())],
                       metadata={b'v2_state': b'BLOCKED_NO_ALLOCATION',
                                 b'valid_split': b'false',
                                 b'reason': b'User section 2A insufficient eligible pool'})
    manifest = ROOT / 'work/v2_split_manifest.parquet'
    pq.write_table(pa.Table.from_pylist([], schema=schema), manifest, compression='zstd')
    gate.update({
        'schema_version': 1,
        'eligible_id_sha256': id_checksum(eligible),
        'eligible_artifact': {'path': eligible_path.relative_to(ROOT).as_posix(), 'sha256': sha(eligible_path)},
        'registry_checksums': {'path': registry.relative_to(ROOT).as_posix(), 'sha256': sha(registry)},
        'split_manifest': {'path': manifest.relative_to(ROOT).as_posix(), 'sha256': sha(manifest),
                           'rows': 0, 'valid_split': False, 'state': 'BLOCKED_NO_ALLOCATION'},
        'sealed_membership_manifests_created': False,
        'population_label_access': {name: 'UNTOUCHED; population not allocated' for name in PROTECTION_ORDER},
        's1_assignment_overlap': 'NOT_APPLICABLE: no allocation',
        'split_assignment_determinism': 'NOT_RUN: insufficient pool',
        'direct_sealed_target_overlap': 'DEFERRED: no label access',
        'existing_global_target_uniqueness': {'path': 'work/integrity_report.md', 'sha256': invariant_sha,
            'line': 40, 'claim': 'Each labelled training target belongs to at most one S1',
            'verification': 'Previously recorded result referenced; not recomputed'},
        'proposals_not_applied': [
            {'proposal': 'Defer all six populations and research until an untouched source is available',
             'active_population_sizes': {name: 0 for name in PROTECTION_ORDER},
             'meaning': 'Suspension only; not a statistically usable reduced split'},
            {'proposal': 'Provide at least 400000 provably untouched S1s, then retain all requested protected quotas',
             'minimum_new_eligible_s1': max(0, 400_000 - len(eligible)),
             'matcher_train': 'remainder; requires additional untouched S1s above 400000'},
        ],
        'eligibility_rule_change': ('A human may explicitly amend the historical-use rule in a later task. '
            'No such exemption is applied; historically audited IDs cannot be described as never accessed.'),
    })
    (ROOT / 'work/v2_split_checksums.json').write_text(json.dumps(gate, indent=2) + '\n', encoding='utf-8')
    lines = ['# V2 split gate: STOP under section 2A', '',
             f"Eligible S1s: **{len(eligible):,}**. Required protected allocations: **400,000**. Shortage: **{gate['shortage_s1']:,}**.", '',
             'No populations were allocated, resized or opened. The empty Parquet is an explicit blocked-state marker, not a valid zero-size split.', '',
             '| Requested population | Requested S1 | Allocated |', '|---|---:|---|']
    lines += [f'| {name} | {count:,} | Not allocated |' for name, count in gate['requested_counts'].items()]
    lines += ['| v2_matcher_train | Remainder | Not allocated |', '',
              '## Proposed revision for human review', '',
              'With zero eligible S1s, no nonzero reduced allocation can satisfy the untouched rule. Proposed temporary allocation is to defer all six populations (zero active S1s); this suspends research and is not applied as a valid split.', '',
              'To resume while retaining the requested firewall, provide at least 400,000 provably untouched S1s; additional S1s are needed for a nonempty future matcher_train. Protection priority remains final_eval, candidate_holdout, threshold, matcher_tune, candidate_research, then matcher_train remainder.', '',
              'Changing the eligibility rule requires explicit human direction. Aggregate-audited IDs remain historically audited even if a later protocol permits their reuse. No exemption has been applied.', '',
              '## Access status', '',
              'All six populations, including candidate_research, have had no V2 label access. Separate sealed membership files do not yet exist because allocation failed. No research GT join, baseline, candidate index, retrieval experiment or matcher training was run.', '',
              f"Empty eligible ID-set checksum (sorted UTF-8 IDs, newline terminated): `{gate['eligible_id_sha256']}`.",
              f"Blocked split marker SHA-256: `{gate['split_manifest']['sha256']}`.", '',
              'Previously established target uniqueness is referenced from `work/integrity_report.md:40`, SHA-256 `' + invariant_sha + '`. Direct sealed target checks remain deferred; labels were not reopened.', '',
              'Reproduce after the registry: `.venv\\Scripts\\python.exe -B scripts\\v2_close_gate.py`.']
    (ROOT / 'work/v2_split_report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    record('v2-preservation', 'Hash-only V1 verification and backup',
           ['scripts/v2_preserve.py', 'work/v2_v1_immutability_manifest.json'],
           command='.venv\\Scripts\\python.exe -B scripts\\v2_preserve.py',
           label_bearing_bytes_hashed_only=True)
    record('v2-historical-source-review', 'Source and existing execution-evidence review',
           ['work/v2_historical_code_evidence.md', 'work/v2_historical_inventory.json'],
           audit_scope='Retrospective phase summary; existing historical aggregates inspected only as execution evidence',
           existing_report_caveat='integrity_report.md contains historical test-source aggregate counts; no real test records queried or used')
    record('v2-registry', 'Historical IDs projected; files hashed without decoding other columns',
           ['scripts/v2_build_registry.py', 'work/v2_historical_touch_registry.parquet',
            'work/v2_historical_touched_s1.parquet', 'work/v2_eligible_s1.parquet'],
           command='.venv\\Scripts\\python.exe -B scripts\\v2_build_registry.py',
           allowed_columns=['S1 identifier', 'split/role selector only'],
           per_source_audit='work/v2_historical_touch_registry.parquet')
    record('v2-insufficient-stop', 'Section 2A stops allocation and every real label reader',
           ['work/v2_split_manifest.parquet', 'work/v2_split_checksums.json'],
           command='.venv\\Scripts\\python.exe -B scripts\\v2_close_gate.py',
           eligible_s1=len(eligible), shortage_s1=gate['shortage_s1'], allocation_performed=False)
    print(json.dumps({'status': gate['status'], 'eligible_s1': len(eligible),
                      'shortage_s1': gate['shortage_s1'], 'allocation_performed': False}))


if __name__ == '__main__':
    main()
