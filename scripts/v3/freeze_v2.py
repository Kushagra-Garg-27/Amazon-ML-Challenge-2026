"""Prepare a new reproduced V2 snapshot. Unexecuted during the capacity pause.

This preserves selected inference files; it does not claim that the research
scorer is portable. The arbitrary-candidate adapter remains a separate gate.
"""
import argparse,json,math,shutil,subprocess,sys
from pathlib import PurePosixPath
from common import ROOT,sha,write_new,execute_gate
from package_release import workspace_path,validate_names

V2=ROOT/'work/v2_matcher_sprint_r1'
REQUIRED_ROLES={'base_model','teacher_model','teacher_policy','model_manifest',
    'feature_spec','decision_policy','calibration','preprocessing','target_df_recipe','requirements'}


def validate_evidence(assessment,reproduction,audit,policy_sha,model_sha):
    if assessment.get('status')!='COMPLETE_ONE_TIME_INTERNAL_ASSESSMENT':
        raise PermissionError('Completed internal assessment required')
    if reproduction.get('status')!='PASS' or not all(reproduction.get(key) is True for key in
        ('exact_score_array_equal','exact_decisions_equal','exact_decision_scores_equal')):
        raise PermissionError('Exact score and decision reproduction required')
    expected=assessment['metrics']['macro_f05'];actual=reproduction['macro_f05']
    if not math.isfinite(expected) or not math.isfinite(actual) or abs(expected-actual)>1e-12:
        raise PermissionError('Assessment metric reproduction differs')
    if assessment.get('policy_sha256_before_labels')!=policy_sha or assessment.get('model_artifact_sha256')!=model_sha:
        raise PermissionError('Assessment is bound to different inference artifacts')
    if audit.get('status')!='PASS' or audit.get('decision_SHA')!=policy_sha or audit.get('selected_model_SHA')!=model_sha:
        raise PermissionError('Integrity audit is bound to different inference artifacts')


def run(args):
    config_path=workspace_path(args.config);config_digest=sha(config_path)
    config=json.loads(config_path.read_text(encoding='utf-8-sig'))
    if config.get('status')!='REVIEWED_V2_SNAPSHOT_INPUTS':raise PermissionError('Reviewed snapshot inventory required')
    destination=workspace_path(args.destination)
    if not destination.is_relative_to(ROOT/'artifacts') or destination.exists():
        raise FileExistsError('A new directory under artifacts is required')
    entries=config['files'];roles={entry['role'] for entry in entries}
    if not REQUIRED_ROLES.issubset(roles):raise PermissionError('Snapshot dependencies missing')
    # Apply the same cross-platform name checks as the release allowlist.
    validate_names(['code/business_entity_resolution/'+e['snapshot_path'] for e in entries])
    if any(e['snapshot_path'].casefold()=='snapshot_manifest.json' for e in entries):
        raise ValueError('Snapshot manifest path is reserved')
    by_role={}
    for entry in entries:
        if entry['role'] in by_role:raise ValueError('Duplicate snapshot role')
        source=workspace_path(entry['source'])
        if source.suffix.lower() not in ('.py','.json','.txt','.md','.toml'):
            raise ValueError('Only explicit inference models, source and metadata may be frozen')
        if sha(source)!=entry['sha256']:raise PermissionError('Snapshot input changed')
        by_role[entry['role']]=entry
    def path(role):return workspace_path(by_role[role]['source'])
    policy=json.loads(path('decision_policy').read_text())
    selected=json.loads(path('model_manifest').read_text())
    if selected.get('model')!='LightGBM':raise PermissionError('Prepared snapshot currently supports selected LightGBM only')
    if sha(path('model_manifest'))!=policy['model_manifest_sha256'] or sha(path('feature_spec'))!=policy['feature_spec_sha256']:
        raise PermissionError('Selected policy dependency binding differs')
    if sha(path('base_model'))!=selected['artifact_SHA']:raise PermissionError('Selected model differs')
    decision=policy['decision']
    if decision['kind']=='residual':
        if 'residual_model' not in by_role or sha(path('residual_model'))!=decision['artifact_SHA']:
            raise PermissionError('Residual dependency missing or changed')
    elif decision['kind']!='global':raise PermissionError('Decision dependency inventory needs review')
    if path('teacher_model')!=(ROOT/'work/final_matcher_model.txt').resolve():raise PermissionError('Unexpected teacher model')
    if path('teacher_policy')!=(ROOT/'work/final_matcher_policy.json').resolve():raise PermissionError('Unexpected teacher policy')
    if path('decision_policy')!=(V2/'decision_policy_refined.json').resolve():raise PermissionError('Selected V2 policy needs review')
    df_recipe=json.loads(path('target_df_recipe').read_text())
    if df_recipe.get('status')!='VERIFIED_COMPLETE_TARGET_RECIPE' or df_recipe.get('labels_used') is not False:
        raise PermissionError('Verified complete-target DF provenance required')
    if not df_recipe.get('indexes') or not df_recipe.get('rebuild_command') or not df_recipe.get('normalization_sha256'):
        raise PermissionError('DF recipe must identify indexes, normalization and exact rebuild command')
    if df_recipe['normalization_sha256']!=sha(path('preprocessing')):
        raise PermissionError('DF normalization differs from frozen preprocessing')
    for index in df_recipe['indexes']:
        if sha(workspace_path(index['path']))!=index['sha256']:raise PermissionError('DF index differs')
    evidence_paths={name:V2/name for name in ('assessment.json','reproduction.json','leakage_audit.json')}
    evidence={name:json.loads(p.read_text()) for name,p in evidence_paths.items()}
    validate_evidence(evidence['assessment.json'],evidence['reproduction.json'],evidence['leakage_audit.json'],
                      sha(path('decision_policy')),sha(path('base_model')))
    for role in ('teacher_model','teacher_policy'):
        relative=path(role).relative_to(ROOT).as_posix()
        teacher=evidence['leakage_audit.json']['frozen_v1'].get(relative,{})
        if teacher.get('expected')!=sha(path(role)) or teacher.get('actual')!=teacher.get('expected'):
            raise PermissionError('Teacher differs from frozen V1 audit')
    predictions=V2/'assessment_predictions.parquet'
    if sha(predictions)!=evidence['assessment.json']['prediction_sha256']:raise PermissionError('Assessment predictions changed')
    # Reproduce now; an old PASS receipt by itself cannot authorize freezing.
    budget=config['reproduction_preflight']
    required=('projected_rows','projected_bytes','partition_count','expected_runtime_seconds','peak_RAM_bytes','temporary_disk_bytes')
    if any(budget.get(k) is None for k in required):raise PermissionError('Reproduction preflight required')
    print(json.dumps({'reproduction_preflight':budget}),flush=True)
    bindings={str(p.relative_to(ROOT)):sha(p) for p in evidence_paths.values()}
    bindings[str(predictions.relative_to(ROOT))]=sha(predictions)
    for entry in entries:bindings[entry['source']]=entry['sha256']
    runtime_files=sorted((ROOT/'code/business_entity_resolution/src/er').rglob('*.py'))
    runtime_files += [ROOT/'scripts/v2_matcher_sprint.py',ROOT/'scripts/v3/freeze_v2.py']
    for source in runtime_files:bindings[source.relative_to(ROOT).as_posix()]=sha(source)
    command=[sys.executable,'-u','-B','scripts/v2_matcher_sprint.py','reproduce']
    completed=subprocess.run(command,cwd=ROOT,text=True,capture_output=True)
    if completed.returncode or not any(line.startswith('REPRODUCED ') for line in completed.stdout.splitlines()):
        raise RuntimeError('Fresh exact reproduction failed: '+completed.stderr[-4000:])
    for source,digest in bindings.items():
        if sha(workspace_path(source))!=digest:raise RuntimeError('Input changed during reproduction')
    if sha(config_path)!=config_digest:raise RuntimeError('Snapshot configuration changed')
    destination.mkdir(parents=True)
    inventory=[]
    for entry in entries:
        relative=PurePosixPath(entry['snapshot_path'])
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path(entry['role']),target)
        if sha(target)!=entry['sha256']:raise RuntimeError('Snapshot copy differs')
        inventory.append({**entry,'bytes':target.stat().st_size})
    # Source files are recorded by hash; adapter packaging is deliberately a later gate.
    write_new(destination/'snapshot_manifest.json',{
        'status':'REPRODUCED_V2_SNAPSHOT','portable_adapter_ready':False,'ready_for_test_inference':False,
        'limitation':'Research scorer paths remain; arbitrary-candidate feature/context parity is required.',
        'config_sha256':config_digest,'files':inventory,'source_and_evidence_sha256':bindings,
        'reproduction':{'command':command,'exit_code':completed.returncode,'stdout':completed.stdout,'stderr':completed.stderr},
        'assessment_scope':evidence['assessment.json'].get('claim_scope'),
        'next_gate':'Implement and parity-test arbitrary-candidate adapter without tuning from assessment.'})
    print(destination,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--config',required=True)
    p.add_argument('--destination',default='artifacts/final_candidate_matcher_v2')
    args=p.parse_args();execute_gate(args);run(args)
