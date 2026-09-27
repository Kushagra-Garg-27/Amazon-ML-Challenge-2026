"""Prepared bounded exact audit of TSV outputs versus the actual scored pairs.

Run only after test inference is frozen. One hexadecimal S1 partition is compared
at a time. This file is preparation code, not evidence of a successful audit.
"""
import argparse,json
from common import ROOT,sha,write_new,execute_gate,literal

def run(args):
    import duckdb
    manifest_path=(ROOT/args.manifest).resolve();manifest_digest=sha(manifest_path)
    manifest=json.loads(manifest_path.read_text())
    if manifest.get('status')!='TEST_INFERENCE_COMPLETE':raise PermissionError('Inference not complete')
    output=(ROOT/args.output).resolve()
    if not output.is_relative_to(ROOT.resolve()) or output.exists():raise FileExistsError('New workspace output required')
    # Verify the exact artifacts and input binding before touching any test rows.
    for kind in ('candidate_parts','score_parts'):
        for part in manifest[kind]:
            path=(ROOT/part['path']).resolve()
            if not path.is_relative_to(ROOT.resolve()) or sha(path)!=part['sha256']:raise RuntimeError('Inference part changed')
    candidate_by_hash={p['sha256'] for p in manifest['candidate_parts']}
    if any(p.get('candidate_input_sha256') not in candidate_by_hash for p in manifest['score_parts']):
        raise RuntimeError('Scores are not bound to candidate input receipts')
    for label in ('matching','candidate'):
        entry=manifest[label];path=(ROOT/entry['path']).resolve()
        if not path.is_relative_to(ROOT.resolve()) or sha(path)!=entry['sha256']:raise RuntimeError('TSV changed')
    budget=manifest['preflight']
    required=('projected_rows','projected_bytes','partition_count','expected_runtime_seconds','peak_RAM_bytes','temporary_disk_bytes')
    if any(k not in budget or budget[k] is None for k in required):raise PermissionError('Measured preflight required')
    print(json.dumps({'audit_preflight':budget,'comparison_partitions':16}),flush=True)
    temp=output.parent/'audit_tmp';temp.mkdir(parents=True,exist_ok=True)
    c=duckdb.connect();c.execute("SET threads=2; SET memory_limit='1000MB'; SET preserve_insertion_order=false")
    c.execute('SET temp_directory=?',[temp.as_posix()]);c.execute("SET max_temp_directory_size='20GB'")
    def tsv(name,path,list_column):
        c.execute(f"CREATE VIEW {name} AS SELECT * FROM read_csv({literal(path)},delim='\t',header=true,all_varchar=true,quote='',escape='',null_padding=false)")
        if [r[0] for r in c.sql(f'DESCRIBE {name}').fetchall()]!=['source1_entity_id',list_column]:raise RuntimeError('Wrong TSV header')
    tsv('matching',ROOT/manifest['matching']['path'],'matched_entity_ids')
    tsv('candidate',ROOT/manifest['candidate']['path'],'candidate_entity_ids')
    c.execute(f"CREATE TABLE universe AS SELECT entity_id FROM read_csv({literal(ROOT/'dataset/test/test_source1.tsv')},delim='\t',header=true,all_varchar=true)")
    targets=' UNION ALL '.join(f"SELECT entity_id FROM read_csv({literal(ROOT/f'dataset/test/test_source{i}.tsv')},delim='\t',header=true,all_varchar=true)" for i in (2,3))
    c.execute('CREATE TABLE target_ids AS '+targets)
    def parts_view(name,items):
        if not items:raise RuntimeError('Missing inference parts')
        c.execute(f'CREATE VIEW {name} AS '+' UNION ALL '.join(f'SELECT * FROM read_parquet({literal(ROOT/p["path"])})' for p in items))
    parts_view('actual_candidates',manifest['candidate_parts']);parts_view('actual_scores',manifest['score_parts'])
    try:
        result=audit_tables(c)
        # A receipt cannot bind bytes different from those examined above.
        for entry in [*manifest['candidate_parts'],*manifest['score_parts'],manifest['matching'],manifest['candidate']]:
            if sha(ROOT/entry['path'])!=entry['sha256']:raise RuntimeError('Input changed during output audit')
        if sha(manifest_path)!=manifest_digest:raise RuntimeError('Inference manifest changed during audit')
        write_new(output,{'status':'PASS',**result,
          'matching_results_sha256':manifest['matching']['sha256'],
          'candidate_pairs_sha256':manifest['candidate']['sha256'],'inference_manifest_sha256':manifest_digest,
          **{key:True for key in ('exact_test_s1_universe','no_duplicate_s1','valid_target_ids','no_duplicate_ids',
            'matches_within_candidates','candidate_set_equals_matcher_input','matcher_scores_cover_candidates',
            'nonnull_pair_ids','boolean_acceptance','finite_decision_scores','partition_totals_match')},
          'scope':'Exact set joins over every partition; no labels read'})
    finally:c.close()
    print(output,flush=True)


def audit_tables(c,expected_count=1732544):
    """Audit preloaded tables; only synthetic tests override the expected count."""
    count,unique=c.sql('SELECT count(*),count(DISTINCT entity_id) FROM universe').fetchone()
    if count!=expected_count or unique!=count:raise RuntimeError('Test S1 universe differs')
    if c.sql("SELECT count(*) FROM universe WHERE entity_id IS NULL OR NOT starts_with(entity_id,'S1-')").fetchone()[0]:
        raise RuntimeError('Invalid raw S1 namespace')
    if c.sql("SELECT count(*) FROM target_ids WHERE entity_id IS NULL OR NOT (starts_with(entity_id,'S2-') OR starts_with(entity_id,'S3-'))").fetchone()[0]:raise RuntimeError('Invalid raw target namespace')
    target_count,target_unique=c.sql('SELECT count(*),count(DISTINCT entity_id) FROM target_ids').fetchone()
    if target_count!=target_unique:raise RuntimeError('Duplicate raw target ID')
    for name in ('matching','candidate'):
        rows,distinct=c.sql(f'SELECT count(*),count(DISTINCT source1_entity_id) FROM {name}').fetchone()
        if rows!=count or distinct!=count:raise RuntimeError('Missing/duplicate S1 rows')
        if c.sql(f'SELECT count(*) FROM {name} o ANTI JOIN universe u ON o.source1_entity_id=u.entity_id').fetchone()[0]:raise RuntimeError('Wrong S1 universe')
    full_counts={}
    for name in ('actual_candidates','actual_scores'):
        if c.sql(f'SELECT count(*) FROM {name} WHERE source1_entity_id IS NULL OR target_entity_id IS NULL').fetchone()[0]:
            raise RuntimeError('Null matcher input/score ID')
        if c.sql(f'SELECT count(*) FROM {name} a ANTI JOIN universe u ON a.source1_entity_id=u.entity_id').fetchone()[0]:
            raise RuntimeError('Matcher input/score outside S1 universe')
        full_counts[name]=c.sql(f'SELECT count(*) FROM {name}').fetchone()[0]
    score_types={r[0]:r[1] for r in c.sql('DESCRIBE actual_scores').fetchall()}
    if score_types.get('accepted')!='BOOLEAN':raise RuntimeError('Accepted must have BOOLEAN type')
    if score_types.get('decision_score') not in ('FLOAT','DOUBLE'):raise RuntimeError('Decision score must be floating point')
    if c.sql('SELECT count(*) FROM actual_scores WHERE accepted IS NULL OR decision_score IS NULL OR NOT isfinite(decision_score)').fetchone()[0]:
        raise RuntimeError('Null acceptance or nonfinite decision score')
    totals={'candidate_pair_count':0,'accepted_match_count':0}
    partition_counts={name:0 for name in full_counts}
    for shard in '0123456789abcdef':
        for name,column in [('candidate','candidate_entity_ids'),('matching','matched_entity_ids')]:
            c.execute(f"""CREATE OR REPLACE TEMP TABLE {name}_pairs AS
              SELECT source1_entity_id,unnest(string_split({column},',')) target_entity_id
              FROM {name} WHERE substr(md5(source1_entity_id),1,1)='{shard}' AND coalesce({column},'')<>''""")
            n,distinct=c.sql(f'SELECT count(*),count(DISTINCT(source1_entity_id,target_entity_id)) FROM {name}_pairs').fetchone()
            if n!=distinct:raise RuntimeError('Duplicate target in TSV list')
            if c.sql(f'SELECT count(*) FROM {name}_pairs p ANTI JOIN target_ids t ON p.target_entity_id=t.entity_id').fetchone()[0]:raise RuntimeError('Invalid target ID')
            totals['candidate_pair_count' if name=='candidate' else 'accepted_match_count']+=n
        for name in ('actual_candidates','actual_scores'):
            c.execute(f"CREATE OR REPLACE TEMP TABLE {name}_part AS SELECT * FROM {name} WHERE substr(md5(source1_entity_id),1,1)='{shard}'")
            n,distinct=c.sql(f'SELECT count(*),count(DISTINCT(source1_entity_id,target_entity_id)) FROM {name}_part').fetchone()
            if n!=distinct:raise RuntimeError('Duplicate matcher input/score pair')
            partition_counts[name]+=n
        def equal(left,right):
            for a,b in ((left,right),(right,left)):
                if c.sql(f'SELECT count(*) FROM {a} a ANTI JOIN {b} b USING(source1_entity_id,target_entity_id)').fetchone()[0]:
                    raise RuntimeError(f'Pair mismatch: {a} vs {b}')
        equal('candidate_pairs','actual_candidates_part');equal('candidate_pairs','actual_scores_part')
        c.execute('CREATE OR REPLACE TEMP VIEW accepted_scores AS SELECT * FROM actual_scores_part WHERE accepted')
        equal('matching_pairs','accepted_scores')
        if c.sql('SELECT count(*) FROM matching_pairs a ANTI JOIN candidate_pairs b USING(source1_entity_id,target_entity_id)').fetchone()[0]:raise RuntimeError('Containment failed')
        print('audited partition',shard,flush=True)
    empty=c.sql("SELECT count(*) FROM matching WHERE coalesce(matched_entity_ids,'')=''").fetchone()[0]
    if partition_counts!=full_counts:raise RuntimeError('Partition coverage differs from full row counts')
    if any(n!=totals['candidate_pair_count'] for n in full_counts.values()):raise RuntimeError('Matcher row totals differ')
    return {**totals,'test_s1_count':count,'matching_row_count':count,'candidate_row_count':count,
            'empty_prediction_count':empty}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--manifest',required=True)
    p.add_argument('--output',required=True);args=p.parse_args();execute_gate(args);run(args)
