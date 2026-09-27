"""Structural audit for multi-seed sister expansion candidates.

Verifies the artifact integrity without reading any labels:
- All S1 are in v2_candidate_research
- All targets exist in the target corpus
- Same-country pairing
- No duplicate pairs
- No self-match
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code/business_entity_resolution/src'))
from er.candidates_v2.runtime import R, W, sha, write_json, log, connect, populations, Monitor

PASS = 'multiseed_sister'


def run():
    os.chdir(ROOT)
    research, sealed = populations()

    manifest_path = R / f'{PASS}_manifest.json'
    if not manifest_path.exists():
        raise FileNotFoundError(f'{PASS} manifest not found')
    manifest = json.loads(manifest_path.read_text())
    if manifest['status'] != 'GENERATED_CHECKSUMMED_PENDING_AUDIT':
        raise RuntimeError(f'Unexpected manifest status: {manifest["status"]}')

    # Verify each shard checksum
    for part in manifest['parts']:
        if sha(ROOT / part['path']) != part['sha256']:
            raise RuntimeError(f'Shard checksum mismatch: {part["shard"]}')

    c = connect(f'{PASS}_audit')
    glob = (R / f'{PASS}_candidates/*.parquet').as_posix()

    # Structural checks
    checks = c.sql(f"""
      SELECT
        count(*) total_rows,
        count(DISTINCT (source1_entity_id, target_entity_id)) unique_pairs,
        count(*) FILTER(WHERE r.entity_id IS NULL) outside_research,
        count(*) FILTER(WHERE t.mid IS NULL) absent_target,
        count(*) FILTER(WHERE s.country_norm <> t.cc) country_mismatch,
        count(*) FILTER(WHERE source1_entity_id = target_entity_id) self_match,
        count(DISTINCT source1_entity_id) s1_count,
        avg(source_rank) mean_rank,
        max(source_rank) max_rank,
        avg(contributing_seeds) mean_contributing_seeds,
        count(*) FILTER(WHERE contributing_seeds > 1) multi_seed_candidates
      FROM read_parquet('{glob}') p
      LEFT JOIN read_parquet('work/v2_candidate_research.parquet') r
        ON p.source1_entity_id = r.entity_id
      LEFT JOIN read_parquet('work/keys/train_s1.parquet') s
        ON p.source1_entity_id = s.entity_id
      LEFT JOIN (
        SELECT entity_id mid, country_norm cc FROM read_parquet('work/keys/train_s2.parquet')
        UNION ALL
        SELECT entity_id, country_norm FROM read_parquet('work/keys/train_s3.parquet')
      ) t ON p.target_entity_id = t.mid
    """).fetchone()

    cols = ['total_rows', 'unique_pairs', 'outside_research', 'absent_target',
            'country_mismatch', 'self_match', 's1_count', 'mean_rank', 'max_rank',
            'mean_contributing_seeds', 'multi_seed_candidates']
    stats = dict(zip(cols, checks))

    # Source distribution
    source_dist = dict(c.sql(f"""
      SELECT target_source, count(*) FROM read_parquet('{glob}') GROUP BY 1 ORDER BY 1
    """).fetchall())

    passed = (
        stats['total_rows'] == stats['unique_pairs']
        and stats['outside_research'] == 0
        and stats['absent_target'] == 0
        and stats['country_mismatch'] == 0
        and stats['self_match'] == 0
    )

    audit = {
        'status': 'PASS' if passed else 'FAIL',
        'pass': PASS,
        'path': f'work/v2_research/{PASS}_candidates/*.parquet',
        'total_rows': stats['total_rows'],
        'unique_pairs': stats['unique_pairs'],
        'duplicate_pairs': stats['total_rows'] - stats['unique_pairs'],
        'outside_research': stats['outside_research'],
        'absent_target': stats['absent_target'],
        'country_mismatch': stats['country_mismatch'],
        'self_match': stats['self_match'],
        's1_count': stats['s1_count'],
        'mean_rank': float(stats['mean_rank']) if stats['mean_rank'] else None,
        'max_rank': stats['max_rank'],
        'mean_contributing_seeds': float(stats['mean_contributing_seeds']) if stats['mean_contributing_seeds'] else None,
        'multi_seed_candidates': stats['multi_seed_candidates'],
        'source_distribution': source_dist,
        'labels_read': False,
    }
    write_json(R / f'{PASS}_structural_audit.json', audit)

    if not passed:
        raise RuntimeError(f'{PASS} structural audit FAILED: {audit}')

    # Update manifest status
    manifest['status'] = 'GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED'
    write_json(manifest_path, manifest)

    log(f'{PASS}_structural_audit_complete',
        command=r'.venv\Scripts\python.exe -B scripts\v2_multiseed_sister_audit.py',
        v2_research_label_read=False,
        audit_sha256=sha(R / f'{PASS}_structural_audit.json'))

    print(json.dumps(audit, indent=2))


if __name__ == '__main__':
    with Monitor(R / f'tmp/{PASS}_audit') as m:
        run()
    write_json(R / f'{PASS}_audit_resources.json', m.result())
