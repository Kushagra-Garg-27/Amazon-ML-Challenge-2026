"""Prepared V3 allowlist packager: validate, stage, ZIP, extract, validate again.

Execution is deliberately gated and has not been run during the capacity pause.
It never overwrites an existing archive, manifest or staging directory.
"""
import argparse,hashlib,json,re,shutil,subprocess,sys,zipfile
from datetime import datetime,timezone
from pathlib import Path,PurePosixPath
from common import ROOT,sha,write_new,execute_gate

REQUIRED={'output/matching_results.tsv','output/candidate_pairs.tsv',
 'code/business_entity_resolution/README.md','code/business_entity_resolution/requirements.txt',
 'Documentation_template.md'}
DENIED={'dataset','ground_truth','work','tmp','temp','.git','.venv','venv','node_modules','__pycache__','cache','caches'}

def safe_relative(name):
    p=PurePosixPath(name)
    if str(p)!=name or '\\' in name or p.is_absolute() or not p.parts or any(x in ('..','.') for x in p.parts):
        raise ValueError('Unsafe archive entry: '+name)
    for part in p.parts:
        if part.rstrip(' .')!=part or any(ord(ch)<32 or ch in '<>:"|?*' for ch in part):
            raise ValueError('Unsafe Windows archive entry: '+name)
        if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])',part.split('.')[0]):
            raise ValueError('Reserved Windows archive entry: '+name)
    if any(x.lower() in DENIED or x.startswith('.') for x in p.parts):raise ValueError('Forbidden entry: '+name)
    if p.suffix.lower() in ('.parquet','.pyc','.duckdb','.env','.log'):raise ValueError('Forbidden artifact: '+name)
    if name not in REQUIRED and not name.startswith('code/business_entity_resolution/'):
        raise ValueError('Outside challenge package allowlist: '+name)
    return p

def validate_names(names):
    canonical=[str(safe_relative(name)).casefold() for name in names]
    if len(canonical)!=len(set(canonical)):raise ValueError('Archive paths collide on Windows')
    all_names=set(canonical)
    for name in canonical:
        if any(str(parent) in all_names for parent in PurePosixPath(name).parents):
            raise ValueError('Archive file is also a parent directory')

def validate_test_receipt(receipt,entries,commit):
    if receipt.get('status')!='PASS' or receipt.get('code_commit')!=commit:
        raise PermissionError('Tests are not bound to the release commit')
    commands=receipt.get('commands',[])
    if receipt.get('tests_run',0)<=0 or not commands or any(not r.get('command') or r.get('exit_code')!=0 for r in commands):
        raise PermissionError('Successful test execution evidence absent')
    tested={r['path']:r['sha256'] for r in receipt.get('source_files',[])}
    for entry in entries:
        if PurePosixPath(entry['archive_path']).suffix.lower() in ('.py','.txt','.json','.toml','.yaml','.yml'):
            if tested.get(entry['source'])!=entry['sha256']:
                raise PermissionError('Packaged source/config was not bound to the test receipt')

def workspace_path(value):
    path=(ROOT/value).resolve()
    if not path.is_relative_to(ROOT.resolve()):raise ValueError('Path outside workspace')
    return path

def validate(stage,test_dir,log_dir,tag):
    cmd=[sys.executable,str(ROOT/'utils/validate_submission.py'),'--matching',str(stage/'output/matching_results.tsv'),
      '--candidate',str(stage/'output/candidate_pairs.tsv'),'--test-dir',str(test_dir)]
    completed=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True)
    write_new(log_dir/(tag+'_validator.json'),{'command':cmd,'exit_code':completed.returncode,
      'stdout':completed.stdout,'stderr':completed.stderr})
    if completed.returncode!=0 or not re.search(r'\bPASS\b',completed.stdout):
        raise RuntimeError(tag+' official validator failed; no release claim')

def run(args):
    config_path=workspace_path(args.config);config=json.loads(config_path.read_text(encoding='utf-8-sig'))
    config_digest=sha(config_path)
    if config.get('status')!='ARCHITECTURE_FROZEN_TEST_INFERENCE_AUDITED':raise PermissionError('Release readiness absent')
    audit_path=workspace_path(config['output_audit']['path'])
    if sha(audit_path)!=config['output_audit']['sha256']:raise PermissionError('Output audit changed')
    audit=json.loads(audit_path.read_text())
    checks=('exact_test_s1_universe','no_duplicate_s1','valid_target_ids','no_duplicate_ids',
      'matches_within_candidates','candidate_set_equals_matcher_input','matcher_scores_cover_candidates',
      'nonnull_pair_ids','boolean_acceptance','finite_decision_scores','partition_totals_match')
    if audit.get('status')!='PASS' or not all(audit.get(x) is True for x in checks):raise PermissionError('Output audit not complete')
    if audit.get('test_s1_count')!=1732544:raise RuntimeError('Unexpected test universe size')
    if audit.get('matching_row_count')!=1732544 or audit.get('candidate_row_count')!=1732544:raise RuntimeError('Output row count differs')
    tests_path=workspace_path(config['tests']['path'])
    if sha(tests_path)!=config['tests']['sha256']:raise PermissionError('Test receipt changed')
    inference_path=workspace_path(config['inference_manifest']['path'])
    inference_digest=sha(inference_path)
    if inference_digest!=config['inference_manifest']['sha256'] or inference_digest!=audit['inference_manifest_sha256']:
        raise PermissionError('Inference manifest differs from output audit')
    inference=json.loads(inference_path.read_text())
    current=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if current!=config['code_commit']:raise RuntimeError('Release commit differs')
    entries=config['files'];names=[e['archive_path'] for e in entries]
    validate_names(names)
    validate_test_receipt(json.loads(tests_path.read_text()),entries,current)
    if len(names)!=len(set(names)) or not REQUIRED.issubset(names):raise RuntimeError('Duplicate or missing required entries')
    if not any(n.startswith('code/business_entity_resolution/src/') and n.endswith('.py') for n in names):
        raise RuntimeError('Runnable source missing')
    # Exact allowlist files and their hashes must be reviewed before packaging.
    for entry in entries:
        safe_relative(entry['archive_path']);source=workspace_path(entry['source'])
        if source.is_symlink() or not source.is_file() or sha(source)!=entry['sha256']:raise RuntimeError('Release input differs')
        if source.suffix.lower()=='.py' or source.name=='requirements.txt':
            committed=subprocess.check_output(['git','show',current+':'+source.relative_to(ROOT).as_posix()],cwd=ROOT)
            if hashlib.sha256(committed).hexdigest()!=entry['sha256']:
                raise PermissionError('Packaged code differs from the named Git commit')
        if source.suffix.lower() in ('.py','.md','.json','.txt'):
            text=source.read_text(encoding='utf-8')
            if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',text):raise RuntimeError('Secret material detected')
            if re.search(r'''(?i)(?:api[_-]?key|password|access[_-]?token)\s*=\s*['"][^'"]{8,}['"]''',text):
                raise RuntimeError('Potential credential in release source')
    output_entries={e['archive_path']:e for e in entries}
    # Each reported inference hash must identify bytes included in this ZIP.
    for field in ('model_sha','retrieval_policy_sha','feature_manifest_sha','threshold_policy_sha','decision_policy_sha'):
        name=config['inference_artifacts'][field]
        if name not in output_entries or output_entries[name]['sha256']!=config[field] or inference.get(field)!=config[field]:
            raise PermissionError('Inference artifact absent or changed: '+field)
    for filename,field in [('output/matching_results.tsv','matching_results_sha256'),('output/candidate_pairs.tsv','candidate_pairs_sha256')]:
        if output_entries[filename]['sha256']!=audit[field]:raise RuntimeError('Audited TSV differs')
    destination=workspace_path(args.destination)
    if destination.exists():raise FileExistsError(destination)
    destination.mkdir(parents=True);stage=destination/'stage';stage.mkdir()
    extracted=destination/'extracted';archive=destination/'broCode_submission.zip'
    for entry in entries:
        target=stage/entry['archive_path'];target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(workspace_path(entry['source']),target)
        if sha(target)!=entry['sha256']:raise RuntimeError('Source changed while staging')
    test_dir=workspace_path('dataset/test')
    validate(stage,test_dir,destination,'original')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
        for entry in sorted(entries,key=lambda e:e['archive_path']):
            info=zipfile.ZipInfo(entry['archive_path'],date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            with (stage/entry['archive_path']).open('rb') as src,z.open(info,'w',force_zip64=True) as dst:
                shutil.copyfileobj(src,dst,8<<20)
    with zipfile.ZipFile(archive) as z:
        if sorted(z.namelist())!=sorted(names):raise RuntimeError('ZIP allowlist mismatch')
        if z.testzip() is not None:raise RuntimeError('ZIP CRC failure')
        for name in z.namelist():safe_relative(name)
        z.extractall(extracted)
    inventory=[]
    for entry in sorted(entries,key=lambda e:e['archive_path']):
        path=extracted/entry['archive_path'];digest=sha(path)
        if digest!=entry['sha256'] or sha(stage/entry['archive_path'])!=digest:raise RuntimeError('Extracted bytes differ')
        inventory.append({'path':entry['archive_path'],'sha256':digest,'bytes':path.stat().st_size})
    validate(extracted,test_dir,destination,'extracted')
    if sha(config_path)!=config_digest or sha(audit_path)!=config['output_audit']['sha256'] or sha(tests_path)!=config['tests']['sha256'] or sha(inference_path)!=inference_digest:
        raise RuntimeError('Release evidence changed during packaging')
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()!=current:
        raise RuntimeError('Git commit changed during packaging')
    manifest={'submission_zip':str(archive),'zip_sha256':sha(archive),'zip_bytes':archive.stat().st_size,
      **{k:audit[k] for k in ('matching_results_sha256','candidate_pairs_sha256','matching_row_count','candidate_row_count',
          'candidate_pair_count','accepted_match_count','empty_prediction_count','test_s1_count')},
      **{k:config[k] for k in ('model_sha','retrieval_policy_sha','feature_manifest_sha','threshold_policy_sha',
          'decision_policy_sha','code_commit','reproducibility_command')},
      'validator_status':'PASS','extracted_validator_status':'PASS','extracted_SHA_match':True,
      'release_config_sha256':config_digest,'output_audit_sha256':sha(audit_path),'test_receipt_sha256':sha(tests_path),
      'inference_manifest_sha256':inference_digest,
      'timestamp':datetime.now(timezone.utc).isoformat(),'files':inventory}
    write_new(destination/'final_submission_manifest.json',manifest)
    print(json.dumps(manifest,indent=2),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--config',required=True)
    p.add_argument('--destination',required=True);args=p.parse_args();execute_gate(args);run(args)
