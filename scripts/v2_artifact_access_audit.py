"""Audit V2 label-derived artifacts against prospectively sealed S1 IDs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.runtime import R,W,sha,write_json,log,populations,connect


def run():
    os.chdir(ROOT)
    research,sealed=populations()
    c=connect('artifact_access_audit')
    reviewed=[]
    sources=[('research_gt.parquet','s1'),('research_truth_counts.parquet','s1'),
             ('v1_link_diagnostics.parquet','s1'),('v1_entity_metrics.parquet','s1'),
             *[(p.name,'source1_entity_id') for p in sorted(R.glob('v1_plus_*.parquet'))]]
    for name,column in sources:
        path=R/name
        if not path.exists(): continue
        stats=c.sql(f"""SELECT count(*) nrows,count(DISTINCT x.{column}) distinct_s1,
          count(*) FILTER(WHERE r.entity_id IS NULL) outside_research
          FROM read_parquet('{path.as_posix()}') x
          LEFT JOIN read_parquet('work/v2_candidate_research.parquet') r
          ON x.{column}=r.entity_id""").fetchone()
        if stats[2]:
            raise PermissionError(f'Label-derived or combined V2 artifact contains out-of-research S1: {name}')
        reviewed.append({'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path),
                         'rows':stats[0],'distinct_s1':stats[1],'outside_research':0,'sealed_overlap':0})
    ledger=[json.loads(line) for line in (W/'v2_access_ledger.jsonl').read_text(encoding='utf-8').splitlines()]
    if any(item.get('sealed_label_read') for item in ledger):
        raise PermissionError('Ledger contains a sealed-label access')
    research_events=[item for item in ledger if item.get('v2_research_label_read')]
    for item in research_events:
        if (item.get('scope') not in (
                'Exact membership filter before target-label decoding; only v2_candidate_research',
                'v2_candidate_research only', 'research-only diagnostic summaries')
                and item.get('operation') not in ('research_label_access_interrupted',
                                                  'research_label_access_complete')):
            raise PermissionError('Unscoped V2 research-label ledger event')
    result={'status':'PASS','terminology':'prospectively sealed v2 populations',
            'reviewed_artifacts':reviewed,'sealed_s1_membership_count':len(sealed),
            'research_s1_membership_count':len(research),
            'research_label_events':len(research_events),'sealed_label_events':0,
            'ledger_events':len(ledger),'ledger_access_model':'command-level event ledger, not an OS syscall trace',
            'historical_aggregate_exposure_disclosed':True,
            'raw_test_data_access':False,'baseline_target_corpus':'complete training S2/S3',
            'direct_sealed_target_overlap':'deferred; no sealed labels read'}
    write_json(R/'access_audit.json',result)
    log('artifact_access_audit',command='.venv\\Scripts\\python.exe -B scripts\\v2_artifact_access_audit.py',
        v2_research_label_read=False,audit_sha256=sha(R/'access_audit.json'))
    print(json.dumps(result,indent=2))


if __name__=='__main__':run()
