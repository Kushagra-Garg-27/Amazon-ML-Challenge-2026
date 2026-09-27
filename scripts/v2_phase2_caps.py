"""Secondary, label-free cap/quota candidate materialization for research S1s."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.ngram import duckdb_grams_sql
from er.candidates_v2.runtime import R,log,sha,write_json,Monitor
from v2_phase2_access import research_only
import duckdb

OUT=ROOT/'work/v2_phase2_r1'
CAP=OUT/'cap_sweep'
PLUS=R/'v1_plus_all.parquet'
SHARDS='0123456789abcdef'


def connection():
    temp=OUT/'tmp/cap_sweep'
    temp.mkdir(parents=True,exist_ok=True)
    c=duckdb.connect()
    c.execute("SET threads=1; SET memory_limit='1500MB'; SET preserve_insertion_order=false")
    c.execute('SET temp_directory=?',[temp.as_posix()])
    c.execute("SET max_temp_directory_size='24GB'")
    return c


def audited(path:Path,query:str,c,configuration:dict) -> dict:
    receipt=path.with_suffix('.json')
    if path.exists():
        if not receipt.exists():raise RuntimeError(f'Unreceipted cap candidate artifact: {path}')
        old=json.loads(receipt.read_text())
        if old['sha256']!=sha(path) or old['configuration']!=configuration:
            raise RuntimeError(f'Cap candidate restart mismatch: {path}')
        return old
    pending=path.with_suffix('.pending.parquet')
    pending.unlink(missing_ok=True)
    print('cap materialize',path.name,flush=True)
    c.execute(f"COPY ({query}) TO '{pending.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)")
    os.replace(pending,path)
    row=c.sql(f"""SELECT count(*),
      count(*)-count(DISTINCT(source1_entity_id,target_entity_id)),
      count(*) FILTER(WHERE r.entity_id IS NULL),
      count(*) FILTER(WHERE t.entity_id IS NULL)
      FROM read_parquet('{path.as_posix()}') x
      LEFT JOIN read_parquet('work/v2_candidate_research.parquet') r
        ON x.source1_entity_id=r.entity_id
      LEFT JOIN (SELECT entity_id FROM read_parquet('work/keys/train_s2.parquet')
        UNION ALL SELECT entity_id FROM read_parquet('work/keys/train_s3.parquet')) t
        ON x.target_entity_id=t.entity_id""").fetchone()
    if any(row[1:]):raise RuntimeError(f'Cap artifact audit failed: {path}: {row}')
    item={'status':'GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED',
      'population':'v2_candidate_research','path':path.relative_to(ROOT).as_posix(),
      'sha256':sha(path),'rows':row[0],'candidate_count':row[0],
      'duplicate_count':row[1],'invalid_id_count':row[2]+row[3],
      'configuration':configuration,'provenance_metadata':{
        'v1_ranks':'frozen research V1 rank artifacts',
        'name4':'complete-target country DF and postings',
        'seed_selection':'none'},'labels_read':False}
    write_json(receipt,item)
    return item


def run() -> None:
    os.chdir(ROOT)
    if len(research_only())!=100000:raise PermissionError('Research allocation changed')
    if not (OUT/'expansion_evaluation.json').exists():
        raise RuntimeError('Step 1 must be measured before secondary cap sweep')
    evidence=json.loads((OUT/'expansion_evaluation.json').read_text())
    if evidence['status']!='COMPLETE':
        raise RuntimeError('Step 1 incomplete')
    CAP.mkdir(exist_ok=True)
    c=connection()
    heavy=(R/'v1_ranks/*_heavy_rank.parquet').as_posix()
    rn=(R/'v1_ranks/*_rank_name.parquet').as_posix()
    ra=(R/'v1_ranks/*_rank_addr.parquet').as_posix()
    h300=CAP/'heavy_101_300.parquet'
    heavy_receipt=audited(h300,f"""SELECT h.s1 source1_entity_id,h.mid target_entity_id,
      h.rk heavy_rank FROM read_parquet('{heavy}') h
      WHERE h.rk BETWEEN 101 AND 300 ORDER BY 1,2""",c,
      {'direction':'raise heavy sorted-name cap','new_rank_interval':[101,300],
       'source':'frozen V1 heavy_rank'})
    source=CAP/'source_rank_51_80.parquet'
    source_query=f"""WITH u AS (
      SELECT s1,mid,4::UTINYINT pass_bit,rsource::USMALLINT nr,
        0::USMALLINT ar,score::FLOAT ns,0::FLOAT ads
      FROM read_parquet('{rn}') WHERE rsource BETWEEN 51 AND 80
      UNION ALL SELECT s1,mid,8::UTINYINT,0::USMALLINT,
        rsource::USMALLINT,0::FLOAT,score::FLOAT
      FROM read_parquet('{ra}') WHERE rsource BETWEEN 51 AND 80)
      SELECT s1 source1_entity_id,mid target_entity_id,
        bit_or(pass_bit)::UTINYINT provenance,max(nr)::USMALLINT name_token_rank,
        max(ar)::USMALLINT address_token_rank,
        max(ns)::FLOAT name_shared_idf,max(ads)::FLOAT address_shared_idf
      FROM u GROUP BY 1,2 ORDER BY 1,2"""
    source_receipt=audited(source,source_query,c,
      {'direction':'raise frozen V1 source token quotas','new_rank_interval':[51,80],
       'sources':['S2','S3'],'rank_artifacts':['name','addr']})
    ngram=json.loads((R/'ngram_preflight.json').read_text())
    df=next(x for x in ngram['results'] if x['n']==4)
    if sha(ROOT/df['df_path'])!=df['df_sha256']:
        raise RuntimeError('Complete-target name4 DF changed')
    posting=R/'name_char4_postings.parquet'
    posting_receipt=json.loads((R/'name_char4_postings_receipt.json').read_text())
    if sha(posting)!=posting_receipt['sha256']:
        raise RuntimeError('Complete-target name4 postings changed')
    char4_dir=CAP/'name4_source_rank_26_40'
    char4_dir.mkdir(exist_ok=True)
    char4_parts=[]
    missing_char4=[shard for shard in SHARDS if not (char4_dir/f'{shard}.parquet').exists()]
    if missing_char4:
        gram=duckdb_grams_sql('nm',4)
        c.execute(f"""CREATE TEMP TABLE selected_name4 AS WITH sources AS (
          SELECT k.entity_id s1,k.country_norm cc,k.name_norm nm
          FROM read_parquet('work/keys/train_s1.parquet') k
          JOIN read_parquet('work/v2_candidate_research.parquet') r USING(entity_id)),
          eligible AS (SELECT g.*,d.df,
            row_number() OVER(PARTITION BY s1 ORDER BY d.df,g.gram) key_rank
            FROM (SELECT s1,cc,{gram} gram FROM sources
              WHERE length(nm) BETWEEN 4 AND 64) g
            JOIN read_parquet('{df['df_path']}') d USING(cc,gram)
            WHERE d.df<=1000)
          SELECT * FROM eligible WHERE key_rank<=4""")
        join_rows=c.sql("""WITH k AS (SELECT cc,gram,count(*) n,max(df) df
          FROM selected_name4 GROUP BY 1,2)
          SELECT sum(n*df) FROM k""").fetchone()[0]
        if join_rows>60_000_000:
            raise RuntimeError('Name4 quota join exceeds prior 60M hard cap')
    for shard in SHARDS:
        char4=char4_dir/f'{shard}.parquet'
        name4_query=f"""WITH hits AS (
          SELECT s.s1,p.mid,p.target_source,count(*) shared_grams,
            sum(ln(1.0+10320219.0/s.df)) evidence
          FROM selected_name4 s JOIN read_parquet('{posting.as_posix()}') p USING(cc,gram)
          WHERE substr(md5(s.s1),1,1)='{shard}'
          GROUP BY 1,2,3), ranked AS (
          SELECT *,row_number() OVER(PARTITION BY s1,target_source
            ORDER BY evidence DESC,shared_grams DESC,mid) source_rank FROM hits)
          SELECT s1 source1_entity_id,mid target_entity_id,
            source_rank::USMALLINT source_rank
          FROM ranked WHERE source_rank BETWEEN 26 AND 40 ORDER BY 1,2"""
        char4_parts.append(audited(char4,name4_query,c,
          {'direction':'raise name character 4-gram per-source quota',
           'old_per_source_quota':25,'new_per_source_quota':40,
           'df_cap':1000,'top_rare_grams_per_s1':4,'shard':shard}))
    name4_record={'status':'GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED',
      'path':char4_dir.relative_to(ROOT).as_posix(),'rows':sum(x['rows'] for x in char4_parts),
      'candidate_count':sum(x['rows'] for x in char4_parts),
      'duplicate_count':sum(x['duplicate_count'] for x in char4_parts),
      'invalid_id_count':sum(x['invalid_id_count'] for x in char4_parts),
      'parts':char4_parts,'population':'v2_candidate_research',
      'configuration':{'old_total_quota':50,'new_total_quota':80,'df_cap':1000},
      'labels_read':False}
    write_json(char4_dir/'manifest.json',name4_record)
    raw={'heavy200':f"SELECT source1_entity_id,target_entity_id FROM read_parquet('{h300.as_posix()}') WHERE heavy_rank<=200",
         'heavy300':f"SELECT source1_entity_id,target_entity_id FROM read_parquet('{h300.as_posix()}')",
         'source80':f"SELECT source1_entity_id,target_entity_id FROM read_parquet('{source.as_posix()}')",
         'name4_80':f"SELECT source1_entity_id,target_entity_id FROM read_parquet('{(char4_dir/'*.parquet').as_posix()}')"}
    raw['combined_h200_s80_n80']=' UNION ALL '.join(raw[x] for x in ('heavy200','source80','name4_80'))
    raw['combined_h300_s80_n80']=' UNION ALL '.join(raw[x] for x in ('heavy300','source80','name4_80'))
    configs=[]
    for name,union in raw.items():
        path=CAP/f'{name}.parquet'
        query=f"""WITH u AS (SELECT source1_entity_id,target_entity_id
          FROM ({union}) GROUP BY 1,2)
          SELECT u.* FROM u ANTI JOIN read_parquet('{PLUS.as_posix()}') b
            ON u.source1_entity_id=b.source1_entity_id AND u.target_entity_id=b.target_entity_id
          ORDER BY 1,2"""
        item=audited(path,query,c,{'name':name,'baseline':'v1_plus_all',
          'heavy_cap':300 if '300' in name else 200 if '200' in name else 100,
          'source_quota':80 if 's80' in name or name=='source80' else 50,
          'name4_total_quota':80 if 'n80' in name or name=='name4_80' else 50})
        configs.append(item)
    manifest={'status':'GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED',
      'population':'v2_candidate_research','parts':configs,
      'raw_artifacts':[heavy_receipt,source_receipt,name4_record],
      'configuration':{'secondary_after_step1':True,
        'tested_heavy_caps':[100,200,300],
        'tested_source_quotas':[[50,50],[80,80]],
        'tested_name4_total_quotas':[50,80],
        'factorial':'individual contrasts and two combined extremes'},
      'labels_read':False}
    write_json(CAP/'manifest.json',manifest)
    log('phase2_cap_candidates_audited',population='v2_candidate_research',
      v2_research_label_read=False,manifest_sha256=sha(CAP/'manifest.json'))
    print(json.dumps({x['configuration']['name']:x['rows'] for x in configs},indent=2))
    c.close()


if __name__=='__main__':
    with Monitor(OUT/'tmp/cap_sweep') as monitor:run()
    write_json(OUT/'cap_sweep_resources.json',monitor.result())
