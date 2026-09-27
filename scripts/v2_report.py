"""Summarize delivered gate evidence and explicitly deferred research outputs."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return json.loads((ROOT / 'work' / name).read_text(encoding='utf-8'))


def main():
    preservation = load('v2_v1_immutability_manifest.json')
    registry = load('v2_historical_touch_checksums.json')
    inventory = load('v2_historical_inventory.json')
    split = load('v2_split_checksums.json')
    tests = load('v2_test_results.json')
    ledger = [json.loads(line) for line in (ROOT / 'work/v2_access_ledger.jsonl').read_text().splitlines()]
    v1, v2 = tests['v1'], tests['v2']
    if split['allocation_performed'] or split['eligible_s1'] != 0:
        raise RuntimeError('This is the zero-eligible-pool stop report, not a research completion report')
    if any(e['v2_research_label_read'] or e['sealed_label_read'] for e in ledger):
        raise RuntimeError('Unexpected label-access ledger entry')
    manifests = inventory['membership_manifests']
    manifest_rows = sum(m['rows'] for m in manifests)
    universe_count = registry['counts']['original_train_s1']
    changed = subprocess.check_output(['git', 'status', '--short', '--untracked-files=all'], cwd=ROOT, text=True)
    if subprocess.run(['git', 'diff', '--quiet', 'release_v1', '--diff-filter=CDMRTUXB'], cwd=ROOT).returncode:
        raise RuntimeError('A pre-existing V1 tracked file changed')
    requested_outputs = [
        ('V1 preservation proof', 'PASS; 59 frozen artifacts, read-only backup, exact tag/source snapshot.'),
        ('Archive/output SHA verification', 'PASS; all three match the submitted values below.'),
        ('Historical-touch registry', f"Delivered; {registry['counts']['registry_sources']} source records with ID and byte checksums, contribution and overlap accounting."),
        ('Exact eligible source pool', 'Delivered; 0 S1, exact old_model_fit minus historically_touched set.'),
        ('V2 split manifests/checksums', 'BLOCKED; explicit empty marker is not a valid allocation. Protected quotas unchanged.'),
        ('Firewall/access ledger', 'Delivered; no V2 label-reading commands. All six populations remain unallocated.'),
        ('V1 baseline on candidate_research', 'NOT RUN: section 2A.'),
        ('Mutually exclusive miss decomposition', 'NOT RUN: section 2A.'),
        ('Non-exclusive opportunity analysis', 'NOT RUN: section 2A.'),
        ('Entity-set/target-to-target analysis', 'NOT RUN: section 2A.'),
        ('Pass preflight estimates/rejections', 'NOT RUN: no research population; no pass accepted or rejected on performance.'),
        ('Implemented retrieval passes', 'NONE; only eligibility gate code was added.'),
        ('Standalone pass results', 'NOT RUN.'),
        ('Incremental pass results', 'NOT RUN.'),
        ('Materialized Pareto frontier', 'DOES NOT EXIST; no candidate artifact was generated.'),
        ('Best candidate-policy research recommendation', 'NONE; eligibility must be resolved first.'),
        ('Full-scale train/test volume projection', 'NOT ESTIMATED; no research operating point or real test data used.'),
        ('Resource requirements/risks', 'Registry used one DuckDB thread and a 512MB DuckDB memory limit; total-process peak RSS was not captured. Retrieval resource needs unknown.'),
        ('Changed files', 'Only V2 additions; list below. V1 tracked-file diff empty.'),
        ('V1/V2 tests', f"V1: {v1['passed']} passed, {v1['failed']} failed, {v1['errors']} errors, {len(v1['skipped'])} explicit skips. V2: {v2['passed']} passed, {v2['failed']} failed, {v2['errors']} errors."),
        ('Access-ledger summary', f'{len(ledger)} phase/run records; detailed source projections in the 598-row registry. Zero V2 research or sealed-label reads.'),
    ]
    lines = [
        '# V2 candidate research: stopped at the eligibility gate', '',
        '**Status: BLOCKED_INSUFFICIENT_POOL. User section 2A requires stopping before V2 label access.**', '',
        '## Exact finding', '',
        '| Quantity | S1 |', '|---|---:|',
        f"| Old model_fit source pool | {registry['old_model_fit_s1']:,} |",
        f"| Old model_fit with documented historical label audit | {registry['touched_model_fit_s1']:,} |",
        '| Eligible V2 S1 | 0 |', '| Required protected allocations | 400,000 |', '| Shortage | 400,000 |', '',
        'The prior matcher split code computed descriptive link counts, singleton counts, mean links and match-count distributions for every one of 1,765,608 model_train S1s. The executed log and checksum report match the exact manifest ID checksum. An ID-only subset check proves every old model_fit S1 was included. The LEFT JOIN also included S1s with no links.', '',
        'The full model_fit exclusion therefore follows from an executed labelled audit. The source-pool role itself contributes no touch. This is separate from the smaller 50,000-S1 fit experiment tiers and the 212,741 fit IDs covered by the selective/grouped artifact union. The 1,443,051 IDs outside that selective union still participated in the broad audit and cannot be certified never accessed.', '',
        'See `work/v2_historical_code_evidence.md`, `work/v2_historical_touch_report.md`, and the Parquet registry for selectors, execution evidence and checksums.', '',
        '## Registry accounting and limitations', '',
        f"Historical union: {registry['historically_touched_s1']:,} S1. Explicit artifact inventory: 583 sources; registry: {registry['counts']['registry_sources']} records including operation scopes and three corrupt-file supersets.", '',
        f'The 11 ID membership manifests contain {manifest_rows:,} ID occurrences and cover {universe_count:,} distinct IDs. All {universe_count:,} IDs recur across manifests because both the original and matcher top-level manifests contain the complete S1 universe. Duplicate occurrences beyond the first: {manifest_rows-universe_count:,}. Each manifest itself has zero duplicate IDs. These membership-only accounting counts do not add touch evidence.', '',
        'Three pre-existing quarantine shards have invalid Parquet footers. Their exact stored memberships remain unverifiable; the registry excludes their smallest proved selected population, candidate_dev, conservatively. They add no IDs beyond the exact executed-audit sources. Every readable historical S1 artifact was projected using ID columns only. Binary hashes include all file bytes without decoding label/feature/score values.', '',
        f"Eligible sorted-ID SHA-256: `{registry['eligible_id_sha256']}` (empty set).",
        'Logical ID hashes are stable reproduction targets. Registry physical hashes identify this run; its elapsed-time fields can differ across reproductions.', '',
        '## V1 preservation', '',
        f"Branch: `{preservation['research_branch']}`. Tag: `release_v1`. Commit: `{preservation['release_commit']}`.", '',
        'A separate read-only submitted ZIP and source snapshot reside in `work/v2_release_v1/`. The source snapshot is checked against a fresh Git archive of the preserved commit. Prior archive extraction/import/validator success is referenced by checksum; real test inference was not rerun.', '',
        '| Artifact | Verified SHA-256 |', '|---|---|',
    ]
    for path in ('broCode_submission.zip', 'output/candidate_pairs.tsv', 'output/matching_results.tsv'):
        lines.append(f"| {path} | `{preservation['artifacts'][path]['actual_sha256']}` |")
    lines += ['', '## Requested deliverables', '', '| # | Deliverable | Result |', '|---:|---|---|']
    lines += [f'| {i} | {name} | {result} |' for i, (name, result) in enumerate(requested_outputs, 1)]
    lines += ['', '## Verification', '',
              f"Command: `{tests['command']}`. Overall exit code: **{tests['exit_code']}**.",
              f"V1: discovered {v1['discovered']}; executed {v1['executed']}; passed {v1['passed']}; failed {v1['failed']}; errors {v1['errors']}; skipped {len(v1['skipped'])}; exit {v1['exit_code']}.",
              f"V2: discovered/executed {v2['executed']}; passed {v2['passed']}; failed {v2['failed']}; errors {v2['errors']}; exit {v2['exit_code']}.", '',
              '**The complete V1 suite was not executed.** Four unchanged tests require real row-level labels. They were explicitly skipped to honor the access boundary; no passing claim is made for them. The V1 frozen-artifact checksum test did execute. Fixture tests use synthetic labels and an existing tiny V1 model training test, not V2 matcher training.', '',
              'Two initial new-runner attempts exited 1 during test discovery because the repository root was absent from its import path. No assertions executed in those attempts. The V2 runner import path was fixed; the reported run then completed with exit 0. V1 files were not changed.', '',
              'Skipped assertions:', '']
    lines += [f"- `{item['test']}` — {item['reason']}" for item in v1['skipped']]
    lines += ['', f"Logs: `{v1['log']}` and `{v2['log']}`.", '',
              'Retrieval, split-assignment and sealed-join tests for unimplemented research are deferred. New tests cover eligibility, duplicate/order handling, exact shortage, fail-closed access and blocked-manifest semantics.', '',
              '## Access confirmations', '',
              '- V1 release, ZIP, both output files and tracked V1 source remain unchanged.',
              '- No V2 labels were accessed, including candidate_research. Candidate_holdout, matcher_train, matcher_tune, threshold and final_eval labels remain untouched; populations have not been allocated.',
              '- No candidate retrieval was run. Complete-target-corpus retrieval is therefore unverified/not applicable; no GT-filtered target corpus was built.',
              '- No V2 matcher, full test candidates, test predictions or leaderboard submission were created.',
              '- No real test records or test-score distributions were inspected. An existing integrity report contained historical test-source aggregate counts alongside its training invariant; this incidental report access is disclosed and was not used for research.',
              '- Access ledger is a phase/run record plus the detailed registry, not an operating-system trace. It records zero real-row/V2 label reads. Historical aggregate reports were inspected as execution evidence and in V1 compatibility assertions.', '',
              '## Proposed allocation revision for human review', '',
              'No nonzero reduced allocation exists under the current rule and zero eligible pool. Proposed temporary revision: defer all six populations with zero active S1s. This is suspension only and has not been recorded as a valid split.', '',
              'For resumption without weakening the firewall, supply at least 400,000 provably untouched S1s and keep the requested quotas; extra S1s feed matcher_train. Protection priority remains final_eval, candidate_holdout, threshold, matcher_tune, candidate_research, then matcher_train.', '',
              'Alternatively, humans would need to explicitly amend the historical-use rule in a later task. No exception for aggregate audits, integrity reads or other past use is assumed. Any permitted reused population must be described honestly as historically accessed.', '',
              '## Reproduction commands', '', '```powershell',
              '.venv\\Scripts\\python.exe -B scripts\\v2_preserve.py',
              '.venv\\Scripts\\python.exe -B scripts\\v2_build_registry.py',
              '.venv\\Scripts\\python.exe -B scripts\\v2_close_gate.py',
              '.venv\\Scripts\\python.exe -B scripts\\v2_verify.py',
              '.venv\\Scripts\\python.exe -B scripts\\v2_preserve.py',
              '.venv\\Scripts\\python.exe -B scripts\\v2_report.py', '```', '',
              '## Changed files', '', '```text', changed.rstrip(), '```', '',
              'Large V2 backup/Parquet files may be ignored by Git; they remain in the workspace and are bound by the V2 manifests.', '',
              '**No candidate-policy RESEARCH RECOMMENDATION was produced because the eligibility gate failed. A VALIDATED V2 POLICY does not exist.**', '']
    out = ROOT / 'work/v2_research_stop_report.md'
    out.write_text('\n'.join(lines), encoding='utf-8')
    print(out)


if __name__ == '__main__':
    main()
