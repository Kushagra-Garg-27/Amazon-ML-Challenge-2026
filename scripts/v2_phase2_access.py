"""Phase 2 membership guard that never decodes sealed population rows."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.runtime import sha
import pyarrow.parquet as pq


def research_only() -> set[str]:
    split=json.loads((ROOT/'work/v2_split_checksums.json').read_text())
    if split['status']!='ALLOCATED_PROSPECTIVE_SEAL':
        raise PermissionError('Prospective allocation not complete')
    if sha(ROOT/'work/v2_historical_touch_reclassification.json')!=split['reclassification_sha256']:
        raise PermissionError('Approved exposure reclassification changed')
    entry=split['populations']['v2_candidate_research']
    path=ROOT/entry['path']
    if entry['s1']!=100000 or sha(path)!=entry['sha256'] or entry['id_sha256']!=(
        'cf894f26d96169c0d88e6c744ddbb073738deea367865f2e712bd59a3d6b9a43'):
        raise PermissionError('Research allocation identity changed')
    ids=pq.read_table(path,columns=['entity_id'])['entity_id'].to_pylist()
    if len(ids)!=100000 or len(set(ids))!=100000:
        raise PermissionError('Research membership count or uniqueness differs')
    # Other population paths, bytes, and rows are deliberately not opened.
    return set(ids)
