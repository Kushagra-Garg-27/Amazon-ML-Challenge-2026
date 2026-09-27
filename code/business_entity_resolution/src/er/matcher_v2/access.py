"""Positive authorization checks; never enumerate sealed memberships."""
import hashlib
import json
from pathlib import Path
import pyarrow.parquet as pq

ROLE_NAMES=('train','model_select','policy_select','assessment')

def v1_tracked_changes(name_status_lines):
    """Return changes to release files; additions are V2/V3, not V1 mutations."""
    return [line for line in name_status_lines if line and not line.startswith('A\t')]

def role_for(s1):
    bucket=int.from_bytes(hashlib.sha256(('matcher-sprint-r1:'+s1).encode()).digest()[:8],'big')%100
    return 0 if bucket<60 else 1 if bucket<75 else 2 if bucket<85 else 3

def assert_authorized(ids,allowed):
    if not set(ids).issubset(allowed):raise PermissionError('S1 outside authorized research population')

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(8<<20),b''):h.update(block)
    return h.hexdigest()

def research_ids(root):
    split=json.loads((root/'work/v2_split_checksums.json').read_text())
    entry=split['populations']['v2_candidate_research']
    path=root/entry['path']
    if entry['id_sha256']!='cf894f26d96169c0d88e6c744ddbb073738deea367865f2e712bd59a3d6b9a43' or sha(path)!=entry['sha256']:
        raise PermissionError('Research identity mismatch')
    ids=sorted(pq.read_table(path,columns=['entity_id'])['entity_id'].to_pylist())
    if len(ids)!=100000 or len(set(ids))!=100000:raise PermissionError('Research allocation changed')
    return ids
