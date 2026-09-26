"""Restartable bounded numeric feature materialization."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import threading
import time

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .exact import exact_features
from .fuzzy import fuzzy_features
from .schema import FEATURES, FEATURE_NAMES, FEATURE_SPEC_VERSION
from .token import token_features

TYPE_MAP={'bool':pa.bool_(),'uint8':pa.uint8(),'uint16':pa.uint16(),'float32':pa.float32()}
ARROW_SCHEMA=pa.schema([pa.field('source1_entity_id',pa.string(),False),pa.field('target_entity_id',pa.string(),False)]+[pa.field(x['name'],TYPE_MAP[x['type']],False) for x in FEATURES],metadata={b'feature_spec_version':FEATURE_SPEC_VERSION.encode()})

class TempMonitor:
    def __init__(self,path: Path): self.path=path; self.peak=0; self.stop=threading.Event(); self.thread=threading.Thread(target=self.poll,daemon=True)
    def poll(self):
      while not self.stop.wait(.1):
       self.peak=max(self.peak,sum(p.stat().st_size for p in self.path.rglob('*') if p.is_file()))
    def __enter__(self): self.thread.start(); return self
    def __exit__(self,*_): self.stop.set(); self.thread.join(); self.poll_once()
    def poll_once(self): self.peak=max(self.peak,sum(p.stat().st_size for p in self.path.rglob('*') if p.is_file()))

def _target_sql():
    cols='entity_id,country_norm,name_norm,name_nosuffix,name_sorted,addr_norm'
    return f"SELECT {cols} FROM read_parquet('work/keys/train_s2.parquet') UNION ALL SELECT {cols} FROM read_parquet('work/keys/train_s3.parquet')"

def _record(row,cols): return dict(zip(cols,row))

def materialize_part(candidate: Path,output: Path) -> dict:
    memory_mb=int(os.environ.get('ER_FEATURE_DUCKDB_MEMORY_MB','700'))
    if not 500 <= memory_mb <= 1200: raise ValueError('ER_FEATURE_DUCKDB_MEMORY_MB must be 500..1200')
    c=duckdb.connect(); c.execute(f"SET memory_limit='{memory_mb}MB'; SET threads=1; SET preserve_insertion_order=false")
    c.execute("SET temp_directory='work/feature_pilot_tmp'"); t0=time.time()
    query=f"""SELECT p.source1_entity_id,p.target_entity_id,p.provenance,p.name_token_rank,p.address_token_rank,
      p.source_balanced_rank,p.heavy_sorted_block,p.name_shared_idf,p.address_shared_idf,
      s.country_norm s_country,s.name_norm s_name_norm,s.name_nosuffix s_name_nosuffix,s.name_sorted s_name_sorted,s.addr_norm s_addr_norm,
      t.country_norm t_country,t.name_norm t_name_norm,t.name_nosuffix t_name_nosuffix,t.name_sorted t_name_sorted,t.addr_norm t_addr_norm
      FROM read_parquet('{candidate.as_posix()}') p
      JOIN read_parquet('work/keys/train_s1.parquet') s ON p.source1_entity_id=s.entity_id
      JOIN ({_target_sql()}) t ON p.target_entity_id=t.entity_id ORDER BY 1,2"""
    cur=c.execute(query); desc=[x[0] for x in cur.description]
    partial=output.with_suffix('.partial.parquet'); partial.unlink(missing_ok=True); writer=pq.ParquetWriter(partial,ARROW_SCHEMA,compression='zstd')
    rows=0; timing={'exact':0.0,'token':0.0,'fuzzy':0.0}; monitor=TempMonitor(Path('work/feature_pilot_tmp')); monitor.__enter__()
    try:
      while True:
        batch=cur.fetchmany(20000)
        if not batch: break
        records=[]
        for r in batch:
          d=_record(r,desc); s={'country_norm':d['s_country'],'name_norm':d['s_name_norm'],'name_nosuffix':d['s_name_nosuffix'],'name_sorted':d['s_name_sorted'],'addr_norm':d['s_addr_norm']}
          t={'country_norm':d['t_country'],'name_norm':d['t_name_norm'],'name_nosuffix':d['t_name_nosuffix'],'name_sorted':d['t_name_sorted'],'addr_norm':d['t_addr_norm']}
          prov=int(d['provenance']); out={'source1_entity_id':d['source1_entity_id'],'target_entity_id':d['target_entity_id'],
            'provenance':prov,'retrieved_sorted_name':bool(prov&1),'retrieved_exact_address':bool(prov&2),
            'retrieved_name_token':bool(prov&4),'retrieved_address_token':bool(prov&8),
            'retrieval_pass_count':sum(bool(prov&b) for b in (1,2,4,8)),
            'name_token_rank':int(d['name_token_rank']),'address_token_rank':int(d['address_token_rank']),
            'source_balanced_rank':int(d['source_balanced_rank']),'heavy_sorted_block':bool(d['heavy_sorted_block']),
            'target_is_s2':d['target_entity_id'].startswith('S2-'),'target_is_s3':d['target_entity_id'].startswith('S3-'),
            'name_shared_idf':float(d['name_shared_idf']),'address_shared_idf':float(d['address_shared_idf'])}
          z=time.perf_counter(); out.update(exact_features(s,t)); timing['exact']+=time.perf_counter()-z
          z=time.perf_counter(); out.update(token_features(s,t)); timing['token']+=time.perf_counter()-z
          z=time.perf_counter(); out.update(fuzzy_features(s,t)); timing['fuzzy']+=time.perf_counter()-z; records.append(out)
        table=pa.Table.from_pylist(records,schema=ARROW_SCHEMA); writer.write_table(table); rows+=len(records)
    finally: writer.close(); c.close(); monitor.__exit__()
    os.replace(partial,output)
    return {'input':candidate.as_posix(),'output':output.as_posix(),'rows':rows,'bytes':output.stat().st_size,'wall_seconds':time.time()-t0,'group_seconds':timing,'peak_temp_bytes':monitor.peak}

def run(candidate_dir: Path,output_dir: Path):
    output_dir.mkdir(parents=True,exist_ok=True); results=[]
    for candidate in sorted(candidate_dir.glob('*.parquet')):
      output=output_dir/candidate.name
      if output.exists():
        meta=pq.read_metadata(output)
        if meta.metadata.get(b'feature_spec_version')!=FEATURE_SPEC_VERSION.encode(): raise RuntimeError('Feature version mismatch')
        results.append({'input':candidate.as_posix(),'output':output.as_posix(),'rows':meta.num_rows,'bytes':output.stat().st_size,'resumed':True}); continue
      print(f'features {candidate.name}',flush=True); results.append(materialize_part(candidate,output))
    out={'feature_spec_version':FEATURE_SPEC_VERSION,'feature_count':len(FEATURES),'parts':results,'rows':sum(x['rows'] for x in results),'bytes':sum(x['bytes'] for x in results)}
    (output_dir/'materialization.json').write_text(json.dumps(out,indent=2)+"\n",encoding='utf-8'); return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--candidates',type=Path,default=Path('work/feature_pilot_candidates')); ap.add_argument('--output',type=Path,default=Path('work/feature_pilot')); a=ap.parse_args(); print(json.dumps(run(a.candidates,a.output),indent=2))

if __name__=='__main__': main()
