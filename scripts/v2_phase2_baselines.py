"""Frozen V1 matcher scoring on the prospectively allocated research S1s.

This runner never reads labels. Research GT is joined only by a later evaluator.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code/business_entity_resolution/src'))
from er.candidates_v2.runtime import R, log, sha, write_json
from v2_phase2_access import research_only
from er.features.materialize import materialize_part
from er.test_pipeline.score import run as score_run
import pyarrow.parquet as pq

OUT = ROOT / 'work/v2_phase2_r1'
V1 = R / 'v1_candidates'
PLUS = R / 'v1_plus_all.parquet'
NEW = OUT / 'plus_all_new_candidates'
EXP_NEW = OUT / 'expansion_new_candidates'
CAP_NEW = OUT / 'cap_new_candidates'


def guard() -> None:
    os.chdir(ROOT)
    research = research_only()
    if len(research) != 100000:
        raise PermissionError('Prospective research allocation failed')
    audited = json.loads((R / 'v1_plus_all_structural_audit.json').read_text())
    if audited['status'] != 'PASS' or audited['sha256'] != sha(PLUS) or audited['rows'] != 18895613:
        raise RuntimeError('v1_plus_all candidate audit differs')
    manifest = json.loads((R / 'v1_baseline_manifest.json').read_text())
    if manifest['status'] != 'GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED':
        raise RuntimeError('V1 research candidate audit missing')
    for part in manifest['parts']:
        if sha(ROOT / part['path']) != part['sha256']:
            raise RuntimeError(f"Frozen V1 research candidate changed: {part['path']}")
    OUT.mkdir(exist_ok=True)


def feature_parts(source_dir: Path, destination: Path) -> None:
    destination.mkdir(exist_ok=True)
    temp = OUT / 'tmp/features'
    results = []
    for candidate in sorted(source_dir.glob('*.parquet')):
        output = destination / candidate.name
        receipt = output.with_suffix('.json')
        candidate_sha = sha(candidate)
        if output.exists():
            if not receipt.exists():
                raise RuntimeError(f'Unreceipted feature artifact: {output}')
            prior = json.loads(receipt.read_text())
            if prior['input_sha256'] != candidate_sha or prior['sha256'] != sha(output):
                raise RuntimeError(f'Feature restart checksum mismatch: {output}')
            results.append(prior)
            continue
        print('feature', candidate.name, flush=True)
        measured = materialize_part(candidate, output, temp_dir=temp)
        rows = pq.read_metadata(output).num_rows
        if rows != pq.read_metadata(candidate).num_rows:
            raise RuntimeError(f'Candidate/feature row mismatch: {candidate}')
        record = {'population': 'v2_candidate_research', 'path':output.relative_to(ROOT).as_posix(),
                  'input_path':candidate.relative_to(ROOT).as_posix(),
                  'input_sha256':candidate_sha, 'sha256':sha(output), 'rows':rows,
                  'duplicate_count':0, 'invalid_id_count':0,
                  'feature_spec_sha256':sha(ROOT / 'work/feature_spec_v1_1.json'),
                  'configuration':{'frozen_feature_builder':'er.features.materialize.materialize_part',
                                   'keys_prefix':'train', 'batch_rows':20000},
                  **{k:v for k,v in measured.items() if k in ('wall_seconds','peak_temp_bytes','group_seconds','bytes')}}
        write_json(receipt, record)
        results.append(record)
    write_json(destination / 'manifest.json', {'status':'COMPLETE','population':'v2_candidate_research',
        'rows':sum(x['rows'] for x in results),'candidate_count':sum(x['rows'] for x in results),
        'duplicate_count':0,'invalid_id_count':0,'parts':results,
        'provenance_metadata':{'candidate_input_receipts':'per-part input_sha256 and path'},
        'configuration':{'frozen_feature_builder':'er.features.materialize.materialize_part',
          'feature_spec_sha256':sha(ROOT/'work/feature_spec_v1_1.json')},
        'labels_read':False})


def make_new_candidates() -> None:
    import duckdb
    NEW.mkdir(exist_ok=True)
    temp=OUT/'tmp/phase2_new_candidates'
    temp.mkdir(parents=True,exist_ok=True)
    c=duckdb.connect()
    c.execute("SET threads=1; SET memory_limit='1500MB'; SET preserve_insertion_order=false")
    c.execute('SET temp_directory=?',[temp.as_posix()])
    c.execute("SET max_temp_directory_size='24GB'")
    plus = PLUS.as_posix()
    v1 = (V1 / '*.parquet').as_posix()
    research = (ROOT / 'work/v2_candidate_research.parquet').as_posix()
    target = "(SELECT entity_id FROM read_parquet('work/keys/train_s2.parquet') UNION ALL SELECT entity_id FROM read_parquet('work/keys/train_s3.parquet'))"
    receipts = []
    partitions=[(i,country) for i in range(8) for country in ('india','us')]
    missing=[(i,country) for i,country in partitions
             if not (NEW/f'part_{i:02d}_{country}.parquet').exists()]
    if missing:
        # Materialize the deduplicated difference once; physical shard copies
        # then stay bounded and restartable without repeating a 19M-row join.
        heavy = (R/'v1_ranks/*_heavy_rank.parquet').as_posix()
        c.execute(f"""CREATE TEMP TABLE new_pairs AS
            SELECT p.source1_entity_id,p.target_entity_id,k.country_norm,
              0::UTINYINT provenance,0::USMALLINT name_token_rank,
              0::USMALLINT address_token_rank,0::USMALLINT source_balanced_rank,
              (h.s1 IS NOT NULL) heavy_sorted_block,
              0::FLOAT name_shared_idf,0::FLOAT address_shared_idf
            FROM read_parquet('{plus}') p
            ANTI JOIN read_parquet('{v1}') b
              ON p.source1_entity_id=b.source1_entity_id AND p.target_entity_id=b.target_entity_id
            LEFT JOIN (SELECT DISTINCT s1 FROM read_parquet('{heavy}')) h
              ON p.source1_entity_id=h.s1
            JOIN read_parquet('work/keys/train_s1.parquet') k
              ON p.source1_entity_id=k.entity_id""")
        n = c.sql('SELECT count(*) FROM new_pairs').fetchone()[0]
        if n != 18895613-15649461:
            raise RuntimeError(f'New candidate difference size mismatch: {n}')
    for shard,country in partitions:
        output = NEW / f'part_{shard:02d}_{country}.parquet'
        receipt = output.with_suffix('.json')
        if output.exists():
            if not receipt.exists():
                raise RuntimeError(f'Unreceipted new candidate shard: {output}')
            prior = json.loads(receipt.read_text())
            if prior['sha256'] != sha(output) or prior['v1_plus_all_sha256'] != sha(PLUS):
                raise RuntimeError(f'New candidate restart checksum mismatch: {output}')
            receipts.append(prior)
            continue
        pending = output.with_suffix('.pending.parquet')
        pending.unlink(missing_ok=True)
        query = f"""SELECT source1_entity_id,target_entity_id,provenance,
            name_token_rank,address_token_rank,source_balanced_rank,
            heavy_sorted_block,name_shared_idf,address_shared_idf
            FROM new_pairs WHERE hash(source1_entity_id)%8={shard}
              AND country_norm='{country}' ORDER BY 1,2"""
        print('new candidates', shard, country, flush=True)
        c.execute(f"COPY ({query}) TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)")
        os.replace(pending, output)
        check = c.sql(f"""SELECT count(*) n,
            count(*)-count(DISTINCT(x.source1_entity_id,x.target_entity_id)) duplicates,
            count(*) FILTER(WHERE r.entity_id IS NULL) invalid_s1,
            count(*) FILTER(WHERE t.entity_id IS NULL) invalid_target
            FROM read_parquet('{output.as_posix()}') x
            LEFT JOIN read_parquet('{research}') r ON x.source1_entity_id=r.entity_id
            LEFT JOIN {target} t ON x.target_entity_id=t.entity_id""").fetchone()
        if any(check[1:]):
            raise RuntimeError(f'New candidate structural audit failed: {output}: {check}')
        record = {'population':'v2_candidate_research','path':output.relative_to(ROOT).as_posix(),
                  'sha256':sha(output),'rows':check[0],'candidate_count':check[0],
                  'duplicate_count':check[1],'invalid_id_count':check[2]+check[3],
                  'provenance_metadata':{'v1_retrieval_pass_and_rank_fields':'frozen feature contract defaults',
                    'heavy_sorted_block':'S1-level frozen V1 rank artifact',
                    'v1_plus_all_provenance':'retained in audited source candidate artifact'},
                  'configuration':{'shard':'hash(source1_entity_id)%8, country_norm',
                    'shard_id':shard,'country':country,
                    'baseline':'v1_plus_all ANTI JOIN frozen V1 candidates'},
                  'v1_plus_all_sha256':sha(PLUS),'labels_read':False}
        write_json(receipt, record)
        receipts.append(record)
    total = sum(x['rows'] for x in receipts)
    if total != 18895613-15649461:
        raise RuntimeError(f'New candidate union size mismatch: {total}')
    write_json(NEW / 'manifest.json', {'status':'GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED',
        'population':'v2_candidate_research','candidate_count':total,'rows':total,
        'duplicate_count':0,'invalid_id_count':0,'parts':receipts,'labels_read':False})
    c.close()
    log('phase2_plus_all_new_candidates_audited',population='v2_candidate_research',
        v2_research_label_read=False,candidates=total,manifest_sha256=sha(NEW/'manifest.json'))


def score_features(feature_dir: Path, score_dir: Path) -> None:
    result = score_run(features=feature_dir, output=score_dir)
    parts = [json.loads(p.read_text()) for p in sorted(score_dir.glob('*.json'))
             if p.name != 'manifest.json']
    if result['rows'] != sum(x['rows'] for x in parts):
        raise RuntimeError('Score receipt count mismatch')
    write_json(score_dir / 'manifest.json', {'status':'COMPLETE','population':'v2_candidate_research',
        'rows':result['rows'],'candidate_count':result['rows'],'duplicate_count':0,
        'invalid_id_count':0,'accepted':result['accepted'],'parts':parts,
        'model_sha256':sha(ROOT/'work/final_matcher_model.txt'),
        'policy_sha256':sha(ROOT/'work/final_matcher_policy.json'),
        'provenance_metadata':{'feature_input_manifest_sha256':sha(feature_dir/'manifest.json')},
        'configuration':{'frozen_scorer':'er.test_pipeline.score.run',
          'threshold':0.61,'batch_candidates':200000},'labels_read':False})


def make_expansion_new_candidates() -> None:
    """Score the deduplicated union of all predeclared one-hop grid pairs once."""
    import duckdb
    pre = json.loads((OUT/'neighbor_preflight.json').read_text())
    paths = []
    sources = []
    for cap in (1000,5000):
        option=pre['caps'][str(cap)]
        if not (option['under_hard_join_cap'] and option['under_temp_disk_cap']):
            continue
        manifest=OUT/'expansion_grid'/f'df{cap}_manifest.json'
        proof=json.loads(manifest.read_text())
        if proof['status']!='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED':
            raise RuntimeError(f'Expansion grid not audited: DF {cap}')
        for part in proof['configurations']:
            if sha(ROOT/part['path'])!=part['sha256']:
                raise RuntimeError('Expansion configuration candidate changed')
        max_point=next(x for x in proof['configurations'] if
            x['configuration']['seed_threshold']==0.61 and
            x['configuration']['per_seed_quota']==20)
        paths.append(ROOT/max_point['path'])
        sources.append({'df_cap':cap,'path':max_point['path'],'sha256':max_point['sha256']})
    if not paths:
        raise RuntimeError('No resource-feasible expansion configuration')
    EXP_NEW.mkdir(exist_ok=True)
    temp=OUT/'tmp/expansion_new_candidates'
    temp.mkdir(parents=True,exist_ok=True)
    c=duckdb.connect()
    c.execute("SET threads=1; SET memory_limit='1500MB'; SET preserve_insertion_order=false")
    c.execute('SET temp_directory=?',[temp.as_posix()])
    c.execute("SET max_temp_directory_size='24GB'")
    partitions=[(i,country) for i in range(8) for country in ('india','us')]
    missing=[(i,country) for i,country in partitions
             if not (EXP_NEW/f'part_{i:02d}_{country}.parquet').exists()]
    if missing:
        union=' UNION ALL '.join(f"SELECT source1_entity_id,target_entity_id FROM read_parquet('{p.as_posix()}')" for p in paths)
        heavy=(R/'v1_ranks/*_heavy_rank.parquet').as_posix()
        c.execute(f"""CREATE TEMP TABLE new_pairs AS WITH u AS (
          SELECT source1_entity_id,target_entity_id FROM ({union}) GROUP BY 1,2)
          SELECT u.source1_entity_id,u.target_entity_id,k.country_norm,
            0::UTINYINT provenance,0::USMALLINT name_token_rank,
            0::USMALLINT address_token_rank,0::USMALLINT source_balanced_rank,
            (h.s1 IS NOT NULL) heavy_sorted_block,
            0::FLOAT name_shared_idf,0::FLOAT address_shared_idf
          FROM u ANTI JOIN read_parquet('{PLUS.as_posix()}') b
            ON u.source1_entity_id=b.source1_entity_id AND u.target_entity_id=b.target_entity_id
          LEFT JOIN (SELECT DISTINCT s1 FROM read_parquet('{heavy}')) h
            ON u.source1_entity_id=h.s1
          JOIN read_parquet('work/keys/train_s1.parquet') k
            ON u.source1_entity_id=k.entity_id""")
    receipts=[]
    for shard,country in partitions:
        path=EXP_NEW/f'part_{shard:02d}_{country}.parquet'
        receipt=path.with_suffix('.json')
        if path.exists():
            if not receipt.exists():raise RuntimeError(f'Unreceipted expansion score candidate shard: {path}')
            old=json.loads(receipt.read_text())
            if old['sha256']!=sha(path) or old['source_configs']!=sources:
                raise RuntimeError('Expansion score candidate restart mismatch')
            receipts.append(old)
            continue
        pending=path.with_suffix('.pending.parquet')
        pending.unlink(missing_ok=True)
        c.execute(f"""COPY (SELECT source1_entity_id,target_entity_id,
          provenance,name_token_rank,address_token_rank,source_balanced_rank,
          heavy_sorted_block,name_shared_idf,address_shared_idf
          FROM new_pairs WHERE hash(source1_entity_id)%8={shard}
            AND country_norm='{country}' ORDER BY 1,2)
          TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        os.replace(pending,path)
        row=c.sql(f"""SELECT count(*),count(*)-count(DISTINCT(source1_entity_id,target_entity_id))
          FROM read_parquet('{path.as_posix()}')""").fetchone()
        if row[1]:raise RuntimeError('Duplicate expansion score candidates')
        item={'population':'v2_candidate_research','path':path.relative_to(ROOT).as_posix(),
          'sha256':sha(path),'rows':row[0],'candidate_count':row[0],
          'duplicate_count':row[1],'invalid_id_count':0,'source_configs':sources,
          'configuration':{'shard':'hash(source1_entity_id)%8, country_norm',
            'shard_id':shard,'country':country,
            'deduplicate_against':'v1_plus_all'},
          'provenance_metadata':{'seed_and_direction':'retained in expansion_grid artifacts',
            'v1_retrieval_pass_and_rank_fields':'frozen feature contract defaults'},
          'labels_read':False}
        write_json(receipt,item)
        receipts.append(item)
    if missing and sum(x['rows'] for x in receipts)!=c.sql('SELECT count(*) FROM new_pairs').fetchone()[0]:
        raise RuntimeError('Expansion score candidate country partition lost rows')
    write_json(EXP_NEW/'manifest.json',{'status':'GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED',
      'population':'v2_candidate_research','rows':sum(x['rows'] for x in receipts),
      'candidate_count':sum(x['rows'] for x in receipts),'duplicate_count':0,
      'invalid_id_count':0,'parts':receipts,'source_configs':sources,'labels_read':False})
    log('phase2_expansion_score_candidates_audited',population='v2_candidate_research',
      v2_research_label_read=False,manifest_sha256=sha(EXP_NEW/'manifest.json'))
    c.close()


def make_cap_new_candidates() -> None:
    """Score each distinct secondary sweep pair once, preserving V1 rank evidence."""
    import duckdb
    cap=OUT/'cap_sweep'
    manifest=json.loads((cap/'manifest.json').read_text())
    if manifest['status']!='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED':
        raise RuntimeError('Secondary cap candidates not audited')
    for part in manifest['parts']:
        if sha(ROOT/part['path'])!=part['sha256']:
            raise RuntimeError('Secondary cap candidate changed')
    union=next(x for x in manifest['parts'] if x['configuration']['name']=='combined_h300_s80_n80')
    CAP_NEW.mkdir(exist_ok=True)
    temp=OUT/'tmp/cap_new_candidates'
    temp.mkdir(parents=True,exist_ok=True)
    c=duckdb.connect()
    c.execute("SET threads=1; SET memory_limit='1500MB'; SET preserve_insertion_order=false")
    c.execute('SET temp_directory=?',[temp.as_posix()])
    c.execute("SET max_temp_directory_size='24GB'")
    partitions=[(i,country) for i in range(8) for country in ('india','us')]
    missing=[(i,country) for i,country in partitions
             if not (CAP_NEW/f'part_{i:02d}_{country}.parquet').exists()]
    if missing:
        base=(ROOT/union['path']).as_posix()
        source=(cap/'source_rank_51_80.parquet').as_posix()
        heavy=(cap/'heavy_101_300.parquet').as_posix()
        hs=(R/'v1_ranks/*_heavy_rank.parquet').as_posix()
        c.execute(f"""CREATE TEMP TABLE new_pairs AS
          SELECT u.source1_entity_id,u.target_entity_id,k.country_norm,
            (coalesce(s.provenance,0) | CASE WHEN h.target_entity_id IS NOT NULL
              THEN 1 ELSE 0 END)::UTINYINT provenance,
            coalesce(s.name_token_rank,0)::USMALLINT name_token_rank,
            coalesce(s.address_token_rank,0)::USMALLINT address_token_rank,
            CASE WHEN coalesce(s.name_token_rank,0)=0 THEN coalesce(s.address_token_rank,0)
              WHEN coalesce(s.address_token_rank,0)=0 THEN s.name_token_rank
              ELSE least(s.name_token_rank,s.address_token_rank) END::USMALLINT source_balanced_rank,
            (x.s1 IS NOT NULL) heavy_sorted_block,
            coalesce(s.name_shared_idf,0)::FLOAT name_shared_idf,
            coalesce(s.address_shared_idf,0)::FLOAT address_shared_idf
          FROM read_parquet('{base}') u
          LEFT JOIN read_parquet('{source}') s USING(source1_entity_id,target_entity_id)
          LEFT JOIN read_parquet('{heavy}') h USING(source1_entity_id,target_entity_id)
          LEFT JOIN (SELECT DISTINCT s1 FROM read_parquet('{hs}')) x
            ON u.source1_entity_id=x.s1
          JOIN read_parquet('work/keys/train_s1.parquet') k
            ON u.source1_entity_id=k.entity_id""")
        if c.sql('SELECT count(*) FROM new_pairs').fetchone()[0]!=union['rows']:
            raise RuntimeError('Cap scoring union count differs')
    receipts=[]
    for shard,country in partitions:
        path=CAP_NEW/f'part_{shard:02d}_{country}.parquet'
        receipt=path.with_suffix('.json')
        if path.exists():
            if not receipt.exists():raise RuntimeError('Unreceipted cap score candidate shard')
            old=json.loads(receipt.read_text())
            if old['sha256']!=sha(path) or old['source_union_sha256']!=union['sha256']:
                raise RuntimeError('Cap score candidate restart mismatch')
            receipts.append(old)
            continue
        pending=path.with_suffix('.pending.parquet')
        pending.unlink(missing_ok=True)
        print('cap scoring candidate',shard,country,flush=True)
        c.execute(f"""COPY (SELECT source1_entity_id,target_entity_id,provenance,
          name_token_rank,address_token_rank,source_balanced_rank,
          heavy_sorted_block,name_shared_idf,address_shared_idf
          FROM new_pairs WHERE hash(source1_entity_id)%8={shard}
            AND country_norm='{country}' ORDER BY 1,2)
          TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        os.replace(pending,path)
        row=c.sql(f"""SELECT count(*),count(*)-count(DISTINCT(source1_entity_id,target_entity_id))
          FROM read_parquet('{path.as_posix()}')""").fetchone()
        if row[1]:raise RuntimeError('Cap score candidate duplicate')
        item={'status':'GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED',
          'population':'v2_candidate_research','path':path.relative_to(ROOT).as_posix(),
          'sha256':sha(path),'rows':row[0],'candidate_count':row[0],
          'duplicate_count':row[1],'invalid_id_count':0,
          'source_union_sha256':union['sha256'],
          'configuration':{'shard':'hash(source1_entity_id)%8, country_norm',
            'shard_id':shard,'country':country},
          'provenance_metadata':{'heavy_sorted':'frozen rank artifact',
            'source_token_rank':'frozen rank artifact','name4_only':'V1 feature defaults'},
          'labels_read':False}
        write_json(receipt,item)
        receipts.append(item)
    if sum(x['rows'] for x in receipts)!=union['rows']:
        raise RuntimeError('Cap score candidate partition lost rows')
    write_json(CAP_NEW/'manifest.json',{'status':'GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED',
      'population':'v2_candidate_research','rows':union['rows'],
      'candidate_count':union['rows'],'duplicate_count':0,'invalid_id_count':0,
      'parts':receipts,'source_union_sha256':union['sha256'],
      'configuration':{'cap_sweep_manifest_sha256':sha(cap/'manifest.json')},'labels_read':False})
    log('phase2_cap_score_candidates_audited',population='v2_candidate_research',
      v2_research_label_read=False,manifest_sha256=sha(CAP_NEW/'manifest.json'))
    c.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['v1-features','v1-scores','new-candidates','new-features','new-scores',
                                          'expansion-new-candidates','expansion-new-features','expansion-new-scores',
                                          'cap-new-candidates','cap-new-features','cap-new-scores'])
    stage = parser.parse_args().stage
    guard()
    if stage == 'v1-features': feature_parts(V1, OUT/'v1_features')
    elif stage == 'v1-scores': score_features(OUT/'v1_features', OUT/'v1_scores')
    elif stage == 'new-candidates': make_new_candidates()
    elif stage == 'new-features': feature_parts(NEW, OUT/'new_features')
    elif stage == 'new-scores': score_features(OUT/'new_features', OUT/'new_scores')
    elif stage == 'expansion-new-candidates': make_expansion_new_candidates()
    elif stage == 'expansion-new-features': feature_parts(EXP_NEW,OUT/'expansion_new_features')
    elif stage == 'expansion-new-scores': score_features(OUT/'expansion_new_features',OUT/'expansion_new_scores')
    elif stage == 'cap-new-candidates': make_cap_new_candidates()
    elif stage == 'cap-new-features': feature_parts(CAP_NEW,OUT/'cap_new_features')
    else: score_features(OUT/'cap_new_features',OUT/'cap_new_scores')


if __name__ == '__main__':
    main()
