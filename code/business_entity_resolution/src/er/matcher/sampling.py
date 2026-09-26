"""Deterministic training-only negative sampling."""
from __future__ import annotations
import hashlib
from pathlib import Path
import duckdb

SEED='negative_sampling_v1_42'

def deterministic_key(target_id: str, seed: str = SEED) -> str:
 return hashlib.md5(f"{target_id}:{seed}".encode()).hexdigest()

def checksum(path: Path) -> str:
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(8<<20),b''): h.update(b)
 return h.hexdigest()

def build(features_glob: str,labels: Path,out_dir: Path) -> dict:
 out_dir.mkdir(parents=True,exist_ok=True); c=duckdb.connect(); c.execute("SET memory_limit='800MB'; SET threads=1")
 c.execute(f"""CREATE TEMP VIEW base AS SELECT f.*,l.y FROM read_parquet('{features_glob}',filename=true) f JOIN read_parquet('{labels.as_posix()}') l USING(source1_entity_id,target_entity_id) WHERE f.filename LIKE '%model_train%'""")
 hard="(retrieval_pass_count::INT*100 + exact_address_norm::INT*80 + exact_name_nosuffix::INT*60 + exact_name_sorted::INT*40 + name_token_set_ratio*20 + address_token_set_ratio*20)"
 policies={
  'hard':f"y=1 OR row_number() over(partition by source1_entity_id,y order by {hard} desc,target_entity_id)<=20",
  'random':f"y=1 OR row_number() over(partition by source1_entity_id,y order by md5(target_entity_id||':{SEED}'),target_entity_id)<=20",
  'hybrid_source_balanced':f"y=1 OR row_number() over(partition by source1_entity_id,y,target_is_s2 order by {hard} desc,md5(target_entity_id||':{SEED}'),target_entity_id)<=10"
 }
 result={}
 for name,pred in policies.items():
  path=out_dir/f'{name}.parquet'; path.unlink(missing_ok=True)
  c.execute(f"""COPY (SELECT * EXCLUDE(rn) FROM (SELECT *,row_number() over(partition by source1_entity_id,y order by target_entity_id) rn FROM (SELECT *,{pred.split(' OR ')[1]} keep FROM base)) WHERE y=1 OR keep ORDER BY source1_entity_id,target_entity_id) TO '{path.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""") if False else None
  if name=='hard': order=f"{hard} desc,target_entity_id"; partition='source1_entity_id,y'
  elif name=='random': order=f"md5(target_entity_id||':{SEED}'),target_entity_id"; partition='source1_entity_id,y'
  else: order=f"{hard} desc,md5(target_entity_id||':{SEED}'),target_entity_id"; partition='source1_entity_id,y,target_is_s2'
  cap=20 if name!='hybrid_source_balanced' else 10
  c.execute(f"""COPY (SELECT * EXCLUDE(rn,filename) FROM (SELECT *,row_number() OVER(PARTITION BY {partition} ORDER BY {order}) rn FROM base) WHERE y=1 OR rn<={cap} ORDER BY source1_entity_id,target_entity_id) TO '{path.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
  row=c.sql(f"""SELECT count(*),sum(y),count(*) filter(where y=0 and ({hard})>100),count(*) filter(where y=0),count(*) filter(where target_is_s2),count(*) filter(where target_is_s3),count(distinct source1_entity_id) FROM read_parquet('{path.as_posix()}')""").fetchone()
  result[name]=dict(zip(('rows','positives','hard_negatives','negatives','s2','s3','s1'),row)); result[name]['sha256']=checksum(path); result[name]['path']=path.as_posix()
 c.close(); return result
