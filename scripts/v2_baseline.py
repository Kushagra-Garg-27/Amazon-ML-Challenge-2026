"""Frozen V1 retrieval for research S1 only; no label reader or GT index."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates import pilot
from er.candidates_v2.runtime import R,W,sha,write_json,log,connect,populations,Monitor


def run():
    os.chdir(ROOT)
    R.mkdir(exist_ok=True)
    research,sealed=populations()
    inputs={}
    for name in ('train_s1','train_s2','train_s3'):
        path=W/'keys'/f'{name}.parquet'
        import pyarrow.parquet as pq
        inputs[name]={'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path),'rows':pq.ParquetFile(path).metadata.num_rows}
    for name in ('name','addr'):
        path=W/'freeze_gate'/f'df_{name}.parquet'
        inputs['df_'+name]={'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path)}
    inputs['policy']={'path':'work/final_candidate_policy.json','sha256':sha(W/'final_candidate_policy.json')}
    inputs['pilot_source']={'path':'code/business_entity_resolution/src/er/candidates/pilot.py',
                             'sha256':sha(ROOT/'code/business_entity_resolution/src/er/candidates/pilot.py')}
    write_json(R/'v1_inputs.json',inputs)
    c=connect('baseline_preflight')
    selection=R/'v1_selection.parquet'
    selection_sql="""SELECT m.entity_id, 'part_'||lpad(cast(('0x'||substr(sha256(m.entity_id||':v2_physical_1'),1,8))::UBIGINT%8 AS VARCHAR),2,'0') split,k.country_norm
       FROM read_parquet('work/v2_candidate_research.parquet') m
       JOIN read_parquet('work/keys/train_s1.parquet') k USING(entity_id) ORDER BY split,country_norm,entity_id"""
    c.execute(f"COPY ({selection_sql}) TO '{selection.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)")
    if c.sql(f"SELECT count(*) FROM read_parquet('{selection.as_posix()}')").fetchone()[0]!=100000:
        raise RuntimeError('Research selection count mismatch')
    groups=c.sql(f"SELECT split,country_norm,count(*) FROM read_parquet('{selection.as_posix()}') GROUP BY1,2 ORDER BY1,2".replace('GROUP BY1','GROUP BY 1').replace('ORDER BY1','ORDER BY 1')).fetchall()
    # Exact key cardinality estimates before frozen joins; no semantics changed.
    preflight=[]
    for country in sorted({row[1] for row in groups}):
        target=pilot._target_sql(country)
        for field in ('name_sorted','addr_norm'):
            n,worst=c.sql(f"""WITH s AS (SELECT k.{field} k,count(*) n FROM read_parquet('work/keys/train_s1.parquet') k
             JOIN read_parquet('work/v2_candidate_research.parquet') r USING(entity_id)
             WHERE k.country_norm=? AND length(k.{field})>0 GROUP BY1),
             t AS (SELECT {field} k,count(*) n FROM ({target}) WHERE length({field})>0 GROUP BY1)
             SELECT coalesce(sum(s.n*t.n),0),coalesce(max(s.n*t.n),0) FROM s JOIN t USING(k)""".replace('GROUP BY1','GROUP BY 1'),params=[country]).fetchone()
            preflight.append({'country':country,'field':field,'exact_total_join_rows':n,'worst_key_join_rows':worst})
        for field,column in [('name','name_nosuffix'),('addr','addr_norm')]:
            n,worst=c.sql(f"""WITH s AS (SELECT tok,count(*) n FROM (
              SELECT unnest(list_distinct(string_split(k.{column},' '))) tok
              FROM read_parquet('work/keys/train_s1.parquet') k JOIN read_parquet('work/v2_candidate_research.parquet') r USING(entity_id)
              WHERE k.country_norm=?) WHERE length(tok)>0 GROUP BY1)
              SELECT coalesce(sum(s.n*d.df),0),coalesce(max(s.n*d.df),0)
              FROM s JOIN read_parquet('work/freeze_gate/df_{field}.parquet') d USING(tok) WHERE d.cc=? AND d.df<=2000""".replace('GROUP BY1','GROUP BY 1'),params=[country,country]).fetchone()
            preflight.append({'country':country,'field':field+'_token','exact_total_join_rows':n,'worst_key_join_rows':worst,'df_max':2000})
    write_json(R/'v1_preflight.json',{'target_corpus':'complete train S2 + S3; country partition only','gt_used':False,
        'physical_shards':8,'bounds':{'max_estimated_country_pass_rows':1500000000,'max_temporary_disk_bytes':24*1024**3},'estimates':preflight})
    if any(row['exact_total_join_rows']>1500000000 for row in preflight):
        raise RuntimeError('Frozen baseline exceeds preflight resource guard; use smaller physical shards after review')
    c.close()
    output=R/'v1_candidates'; output.mkdir(exist_ok=True)
    ranks=R/'v1_ranks'; ranks.mkdir(exist_ok=True)
    receipts=[]
    os.environ['ER_DUCKDB_MEMORY_MB']='700'
    original_connect=pilot.connect
    def bounded_connect(temp):
        db=original_connect(temp)
        db.execute("SET max_temp_directory_size='24GB'")
        return db
    pilot.connect=bounded_connect
    for split,country,count in groups:
        path=output/f'{split}_{country}.parquet'; receipt=path.with_suffix('.json')
        if path.exists() and not receipt.exists():
            # The final path appears only after pilot's atomic rename. Preserve
            # that completed partition if a crash happened before its receipt.
            import pyarrow.parquet as pq
            rank_files=sorted(ranks.glob(path.stem+'_*.parquet'))
            if len(rank_files)==3:
                try:
                    footer=pq.ParquetFile(path)
                    if {'source1_entity_id','target_entity_id','provenance'} <= set(footer.schema_arrow.names):
                        recovered={'split':split,'country':country,'s1':count,
                                   'candidates':footer.metadata.num_rows,'bytes':path.stat().st_size,
                                   'wall_seconds':0.,'restart_recovered_completed_partition':True,
                                   'sampled_peak_process_tree_rss_bytes':None,'sampled_peak_temp_disk_bytes':None,
                                   'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path),'inputs':inputs,
                                   'rank_artifacts':[{'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p)} for p in rank_files]}
                        write_json(receipt,recovered)
                except Exception:
                    pass
        if path.exists() and receipt.exists():
            data=json.loads(receipt.read_text())
            if data['sha256']!=sha(path) or data['inputs']!=inputs:
                raise RuntimeError('Restart artifact/input checksum mismatch')
            if any(sha(ROOT/x['path'])!=x['sha256'] for x in data['rank_artifacts']):
                raise RuntimeError('Ranking evidence changed on restart')
            receipts.append(data); continue
        print('V1 frozen baseline',split,country,count,flush=True)
        with Monitor(output/'tmp') as monitor:
            result=pilot.materialize_group(selection,split,country,path,rank_output=ranks)
        result.update(monitor.result()); result.update(path=path.relative_to(ROOT).as_posix(),sha256=sha(path),inputs=inputs)
        result['rank_artifacts']=[{'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p)} for p in sorted(ranks.glob(path.stem+'_*.parquet'))]
        write_json(receipt,result);receipts.append(result)
        print('completed',split,country,result['candidates'],'seconds',round(result['wall_seconds'],1),flush=True)
    c=connect('baseline_audit')
    glob=(output/'*.parquet').as_posix()
    duplicates=c.sql(f"SELECT count(*)-count(DISTINCT(source1_entity_id,target_entity_id)) FROM read_parquet('{glob}')").fetchone()[0]
    outside=c.sql(f"SELECT count(*) FROM read_parquet('{glob}') c ANTI JOIN read_parquet('work/v2_candidate_research.parquet') r ON c.source1_entity_id=r.entity_id").fetchone()[0]
    if duplicates or outside: raise RuntimeError('Candidate identity/scope audit failed')
    manifest={'status':'GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED','gt_used':False,'target_corpus':'complete training S2/S3',
              'research_s1':len(research),'duplicates':duplicates,'nonresearch_pairs':outside,'parts':receipts,
              'candidates':sum(x['candidates'] for x in receipts),'artifact_bytes':sum(x['bytes'] for x in receipts),
              'wall_seconds':sum(x['wall_seconds'] for x in receipts),'inputs':inputs,
              'selection_sha256':sha(selection)}
    write_json(R/'v1_baseline_manifest.json',manifest)
    log('frozen_v1_baseline_complete',command='.venv\\Scripts\\python.exe -B scripts\\v2_baseline.py',
        v2_research_label_read=False,manifest_sha256=sha(R/'v1_baseline_manifest.json'),candidates=manifest['candidates'])
    print(json.dumps({k:v for k,v in manifest.items() if k not in ('parts','inputs')},indent=2))


if __name__=='__main__': run()
