"""V3 preparation utilities. No computation starts on import."""
from pathlib import Path
import hashlib
import json
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
OUT=ROOT/'work/v3_research_r1'

def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(8<<20),b''):digest.update(block)
    return digest.hexdigest()

def write_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2,allow_nan=False);stream.write('\n')

def execute_gate(args):
    if not args.execute:
        raise SystemExit('Prepared only. Execution requires --execute after capacity is restored and tests pass.')

def research_connection(stage):
    # Single permitted membership file; do not import the retired populations().
    from er.matcher_v2.access import research_ids,role_for
    import pyarrow as pa
    import duckdb
    allowed=research_ids(ROOT)
    temp=OUT/'tmp'/stage;temp.mkdir(parents=True,exist_ok=True)
    con=duckdb.connect()
    con.execute("SET threads=2; SET memory_limit='1000MB'; SET preserve_insertion_order=false")
    con.execute('SET temp_directory=?',[temp.as_posix()])
    con.execute("SET max_temp_directory_size='20GB'")
    # Only development role identities are exposed to these retrieval queries.
    ids=[s1 for s1 in allowed if role_for(s1)!=3]
    con.register('authorized_development',pa.table({'s1':ids}))
    return con,len(ids)

def literal(path):
    return "'"+Path(path).resolve().as_posix().replace("'","''")+"'"
