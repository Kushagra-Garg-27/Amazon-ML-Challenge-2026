"""Bounded materialization with label-free feature construction and train-only sampling."""
from pathlib import Path
import json
import math
import os
import time
import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from .access import research_ids,assert_authorized,role_for,sha,ROLE_NAMES
from .features import name_features,address_features,script_features,cross_features,weighted_overlap,GROUPS
from er.candidates_v2.runtime import Monitor

ROOT=Path(__file__).resolve().parents[5]
OUT=ROOT/'work/v2_matcher_sprint_r1'
OLD=ROOT/'work/v2_phase2_r1'
V1=json.loads((ROOT/'work/final_matcher_policy.json').read_text())['feature_order']
GROUPS['name']=GROUPS['name']+['name_weighted_jaccard']
GROUPS['address']=GROUPS['address']+['addr_weighted_jaccard']
EXTRA=sum(GROUPS.values(),[])
FEATURES=V1+EXTRA

def write_once(path,value):
    if path.exists():raise FileExistsError(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')

def ledger(row):
    m=row.get('metrics',{})
    row={**row,'matcher_macro_f0.5':m.get('macro_f05'),
      'pair_recall':m.get('candidate_pair_recall'),'oracle_macro_f0.5':m.get('oracle_macro_f05'),
      **{k:m.get(k) for k in ('precision','recall','singleton_f05','underprediction_rate','overprediction_rate')},
      'S2_recall':m.get('source_slices',{}).get('True',{}).get('recall'),
      'S3_recall':m.get('source_slices',{}).get('False',{}).get('recall'),
      'address_present':m.get('address_slices',{}).get('False'),
      'address_missing':m.get('address_slices',{}).get('True'),
      'script_conflict':m.get('script_slices',{}).get('True'),
      'peak_RSS':process_peak_rss(),'peak_RSS_scope':'process lifetime high-water mark, conservative for later experiments'}
    path=OUT/'experiments.jsonl'
    if path.exists() and any(json.loads(x)['experiment_id']==row['experiment_id'] for x in path.read_text().splitlines()):
        raise FileExistsError(row['experiment_id'])
    with path.open('a',encoding='utf-8') as f:f.write(json.dumps(row,allow_nan=False)+'\n')

def process_peak_rss():
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD),('peak',ctypes.c_size_t),
          ('working',ctypes.c_size_t),('pool_peak',ctypes.c_size_t),('pool',ctypes.c_size_t),
          ('nonpaged_peak',ctypes.c_size_t),('nonpaged',ctypes.c_size_t),
          ('pagefile',ctypes.c_size_t),('pagefile_peak',ctypes.c_size_t)]
    p=Counters();p.cb=ctypes.sizeof(p)
    ctypes.windll.kernel32.GetCurrentProcess.restype=wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),ctypes.byref(p),p.cb):
        raise OSError('Cannot measure RSS')
    return int(p.peak)

def connection(stage):
    temp=OUT/'tmp'/stage;temp.mkdir(parents=True,exist_ok=True)
    c=duckdb.connect();c.execute("SET threads=2; SET memory_limit='1000MB'; SET preserve_insertion_order=false")
    c.execute('SET temp_directory=?',[temp.as_posix()]);c.execute("SET max_temp_directory_size='20GB'")
    return c

def receipt(path,**kwargs):
    value={'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path),'rows':pq.read_metadata(path).num_rows,
      'population':'v2_candidate_research','sealed_membership_read':False,'sealed_labels_read':False,**kwargs}
    write_once(path.with_suffix('.json'),value)
    return value

def allocation():
    allowed=research_ids(ROOT)
    path=OUT/'allocation.parquet'
    if not path.exists():
        pq.write_table(pa.table({'s1':allowed,'entity_index':np.arange(len(allowed),dtype=np.int32),
          'role':np.array([role_for(x) for x in allowed],np.uint8)}),path,compression='zstd')
        receipt(path,roles=dict(enumerate(ROLE_NAMES)),split_seed='matcher-sprint-r1')
    table=pq.read_table(path)
    assert_authorized(table['s1'].to_pylist(),set(allowed))
    if sha(path)!=json.loads(path.with_suffix('.json').read_text())['sha256']:raise PermissionError('Allocation changed')
    return table

def prepare():
    a=allocation();c=connection('prepare')
    c.execute(f"CREATE VIEW alloc AS SELECT * FROM read_parquet('{(OUT/'allocation.parquet').as_posix()}')")
    all_scores=' UNION ALL '.join(f"SELECT * FROM read_parquet('{(OUT/('baseline_'+x+'_scores')/'*.parquet').as_posix()}')" for x in ('v1','new'))
    c.execute(f'CREATE VIEW scores AS {all_scores}')
    n=c.sql('SELECT count(*) FROM scores s ANTI JOIN alloc a ON s.source1_entity_id=a.s1').fetchone()[0]
    if n:raise PermissionError('Unauthorized scoring rows')
    sources=OUT/'source_records.parquet'
    if not sources.exists():
        c.execute(f"COPY (SELECT k.* FROM read_parquet('work/keys/train_s1.parquet') k JOIN alloc a ON k.entity_id=a.s1) TO '{sources.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)")
        receipt(sources,provenance='observable S1 fields filtered to authorized research')
    context=OUT/'context.parquet'
    if not context.exists():
        c.execute(f"""COPY (WITH g AS (SELECT source1_entity_id s1,target_is_s2,
          count(*) source_candidate_count,max(score) top_score,
          count(*) FILTER(WHERE score>=.61) high_count,
          list(score ORDER BY score DESC)[2] second_score FROM scores GROUP BY 1,2)
          SELECT *,sum(source_candidate_count) OVER(PARTITION BY s1) candidate_count,
            top_score-coalesce(second_score,0) score_gap FROM g)
          TO '{context.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        receipt(context,provenance='frozen model scores; full candidate set, no labels')
    anchors=OUT/'anchors.parquet'
    if not anchors.exists():
        c.execute("""CREATE TEMP TABLE anchor_ids AS SELECT * FROM (
          SELECT source1_entity_id s1,target_entity_id mid,score,
          row_number() OVER(PARTITION BY source1_entity_id,target_is_s2 ORDER BY score DESC,target_entity_id) rk
          FROM scores WHERE score>=.61) WHERE rk<=2""")
        c.execute(f"""COPY (SELECT a.*,t.name_norm AS "name",t.addr_norm addr FROM anchor_ids a JOIN
          (SELECT entity_id,name_norm,addr_norm FROM read_parquet('work/keys/train_s2.parquet') UNION ALL
           SELECT entity_id,name_norm,addr_norm FROM read_parquet('work/keys/train_s3.parquet')) t
          ON a.mid=t.entity_id ORDER BY s1,rk,mid) TO '{anchors.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        receipt(anchors,provenance='top 2 frozen-score >=0.61 target records per source; no labels')
    specs=[]
    for group,cols in GROUPS.items():
        for name in cols:
            specs.append({'name':name,'dtype':'float32','group':group,
             'input_provenance':'frozen inference scores and same-S1 candidate attributes' if group in ('entity','cross') else 'normalized observable name/address; complete-target token DF',
             'leakage_justification':'pure attributes or frozen V1 predictions; no outcome input',
             'missing':'zero similarity when required text absent; explicit missing indicator',
             'deterministic':True})
    write_once(OUT/'feature_spec.json',{'version':'matcher_sprint_r1','v1_features':V1,'added':specs,
      'feature_order':FEATURES,'groups':GROUPS,'identity_and_label_columns_excluded':True,
      'idf':'ln(1+10320219/DF), computed from complete training target records, country specific',
      'transliteration':'local Devanagari consonant/vowel table and final-schwa heuristic; other scripts not translated',
      'locality':'nonnumeric address-token proxy; no claimed city/state parser',
      'redundancy':'QRatio equals ratio on nonempty normalized strings; V1 already contains ratio, partial, token-sort and token-set'} )
    print('roles',[(ROLE_NAMES[i],sum(np.asarray(a['role'])==i)) for i in range(4)],flush=True)
    c.close()

def materialize():
    alloc=allocation();allowed=set(alloc['s1'].to_pylist())
    destination=OUT/'features';destination.mkdir(exist_ok=True)
    c=connection('features')
    c.execute(f"CREATE VIEW alloc AS SELECT * FROM read_parquet('{(OUT/'allocation.parquet').as_posix()}')")
    c.execute("CREATE TEMP TABLE train_gt AS SELECT g.* FROM read_parquet('work/v2_research/research_gt.parquet') g JOIN alloc a USING(s1) WHERE a.role=0")
    parts=[];t0=time.perf_counter()
    for kind in ('v1','new'):
      for source in sorted((OLD/(kind+'_features')).glob('*.parquet')):
        name=kind+'_'+source.name;dest=destination/name
        if dest.exists():
            old=json.loads(dest.with_suffix('.json').read_text())
            if old['sha256']!=sha(dest):raise RuntimeError('Feature restart checksum differs')
            parts.append(old);continue
        country=source.stem.rsplit('_',1)[-1]
        weights=[]
        for suffix in ('name','addr'):
            rows=c.sql(f"SELECT tok,df FROM read_parquet('work/freeze_gate/df_{suffix}.parquet') WHERE cc='{country}'").fetchall()
            weights.append({token:math.log1p(10320219/df) for token,df in rows});del rows
        scores=OUT/('baseline_'+kind+'_scores')/source.name
        query=f"""WITH chosen AS (SELECT f.*,s.score v1_score,a.entity_index,a.role,
          CASE WHEN a.role=0 THEN (g.mid IS NOT NULL)::TINYINT ELSE -1 END y_train,
          (hash(f.source1_entity_id||f.target_entity_id)%16=0) uniform_keep
          FROM read_parquet('{source.as_posix()}') f JOIN read_parquet('{scores.as_posix()}') s
          USING(source1_entity_id,target_entity_id) JOIN alloc a ON f.source1_entity_id=a.s1
          LEFT JOIN train_gt g ON f.source1_entity_id=g.s1 AND f.target_entity_id=g.mid
          WHERE a.role<>0 OR g.mid IS NOT NULL OR s.score>=.05 OR hash(f.source1_entity_id||f.target_entity_id)%16=0)
          SELECT p.*,k.name_norm sn,k.addr_norm sa,t.name_norm tn,t.addr_norm ta,
          ctx.candidate_count,ctx.source_candidate_count,ctx.top_score,coalesce(ctx.second_score,0) second_score,
          ctx.score_gap,ctx.high_count FROM chosen p
          JOIN read_parquet('{(OUT/'source_records.parquet').as_posix()}') k ON p.source1_entity_id=k.entity_id
          JOIN (SELECT entity_id,name_norm,addr_norm FROM read_parquet('work/keys/train_s2.parquet') WHERE country_norm='{country}'
          UNION ALL SELECT entity_id,name_norm,addr_norm FROM read_parquet('work/keys/train_s3.parquet') WHERE country_norm='{country}') t ON p.target_entity_id=t.entity_id
          JOIN read_parquet('{(OUT/'context.parquet').as_posix()}') ctx ON p.source1_entity_id=ctx.s1 AND p.target_is_s2=ctx.target_is_s2
          ORDER BY p.source1_entity_id,p.target_entity_id"""
        anchors={}
        # Four anchors per S1 is a hard bound; same-source/self removed in pure feature code.
        for row in pq.read_table(OUT/'anchors.parquet').to_pylist():anchors.setdefault(row['s1'],[]).append(row)
        start=time.perf_counter();reader=c.execute(query).fetch_record_batch(10000)
        writer=None;count=positive=0;partial=dest.with_suffix('.pending.parquet')
        print('features',name,flush=True)
        for batch in reader:
            b=pa.Table.from_batches([batch]);ids=b['source1_entity_id'].to_pylist();mids=b['target_entity_id'].to_pylist()
            assert_authorized(ids,allowed)
            columns={x:b[x] for x in ['source1_entity_id','target_entity_id','entity_index','role','y_train','uniform_keep',*V1]}
            computed={x:[] for x in EXTRA if x not in GROUPS['entity']}
            for s1,mid,sn,sa,tn,ta in zip(ids,mids,*(b[x].to_pylist() for x in ('sn','sa','tn','ta'))):
                values={**name_features(sn,tn),**address_features(sa,ta),**script_features(sn,tn),
                  **cross_features({'mid':mid,'name':tn,'addr':ta},anchors.get(s1,[])),
                  'name_weighted_jaccard':weighted_overlap(sn,tn,weights[0]),
                  'addr_weighted_jaccard':weighted_overlap(sa,ta,weights[1])}
                for x,v in values.items():computed[x].append(v)
            for x,values in computed.items():columns[x]=pa.array(values,type=pa.float32())
            for x in GROUPS['entity']:
                values=np.asarray(b['v1_score'])-np.asarray(b['top_score']) if x=='relative_to_top' else np.asarray(b[x])
                columns[x]=pa.array(values,type=pa.float32())
            table=pa.table(columns)
            if writer is None:writer=pq.ParquetWriter(partial,table.schema,compression='zstd')
            writer.write_table(table);count+=len(table);positive+=int((np.asarray(b['y_train'])==1).sum())
        if writer:writer.close()
        partial.replace(dest)
        parts.append(receipt(dest,train_positives=positive,input_feature_sha256=sha(source),
          wall_seconds=time.perf_counter()-start,configuration='all nontraining candidates; all retrieved train positives; score>=.05 or 1/16 hash negative sample'))
        print('completed',name,count,'seconds',round(time.perf_counter()-start),flush=True)
        del anchors,weights
    expected=c.sql("SELECT count(*) FROM train_gt g JOIN read_parquet('work/v2_research/v1_plus_all.parquet') p ON g.s1=p.source1_entity_id AND g.mid=p.target_entity_id").fetchone()[0]
    if sum(x['train_positives'] for x in parts)!=expected:raise RuntimeError('Training positive coverage lost')
    write_once(destination/'manifest.json',{'status':'COMPLETE','parts':parts,'rows':sum(x['rows'] for x in parts),
      'retrieved_train_positives':expected,'positive_coverage_of_retrieved':1.,
      'candidate_absent_positives':'remain in truth denominators; cannot fabricate noncandidate training pairs',
      'wall_seconds':time.perf_counter()-t0,'allocation_sha256':sha(OUT/'allocation.parquet'),'feature_spec_sha256':sha(OUT/'feature_spec.json')})
    c.close()
