"""Quick timing test: how long to load art_val from parquet?"""
import duckdb, time, sys
sys.stdout.reconfigure(encoding='utf-8')
con = duckdb.connect()
con.execute("SET memory_limit='1GB'; SET threads=2;")
con.execute("SET preserve_insertion_order=false;")

t0 = time.time()
print('Loading s1v...', flush=True)
con.execute("""CREATE TABLE s1v AS SELECT entity_id FROM read_parquet('work/keys/train_s1.parquet')
    WHERE entity_id IN (SELECT entity_id FROM read_parquet('work/split_s1.parquet') WHERE split='val')""")
nv = con.sql('SELECT count(*) FROM s1v').fetchone()[0]
print(f'  s1v: {nv} rows, {time.time()-t0:.1f}s', flush=True)

t1 = time.time()
print('Loading art_val with semi-join...', flush=True)
con.execute("""CREATE TABLE art_val AS SELECT source1_entity_id s1, target_entity_id mid
    FROM read_parquet('work/final_candidate_provenance.parquet')
    WHERE source1_entity_id IN (SELECT entity_id FROM s1v)""")
na = con.sql('SELECT count(*) FROM art_val').fetchone()[0]
print(f'  art_val: {na} rows, {time.time()-t1:.1f}s', flush=True)
print(f'Total: {time.time()-t0:.1f}s', flush=True)
con.close()
