"""Audit which token pass generated the 61,897 capping-loss links.
"""
import sys
import duckdb

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

con = duckdb.connect()
con.execute("SET memory_limit='2GB'; SET threads=1;")
con.execute("SET temp_directory='work/duckdb_tmp'; SET preserve_insertion_order=false;")

print("Loading validation GT and S1...")
con.execute("""
CREATE TEMP TABLE s1v AS
SELECT a.entity_id, a.country_norm cc, a.name_nosuffix sns, a.name_sorted ssrt, a.addr_norm sad
FROM read_parquet('work/keys/train_s1.parquet') a
JOIN read_parquet('work/split_s1.parquet') s ON a.entity_id=s.entity_id AND s.split='val'
""")

con.execute("""
CREATE TEMP TABLE gtp AS
SELECT g.source1_entity_id s1, trim(x) mid
FROM read_csv('dataset/train/train_ground_truth.tsv', delim='\\t', header=true, quote='', all_varchar=true) g,
     UNNEST(string_split(g.matched_entity_ids, ',')) AS u(x)
WHERE g.matched_entity_ids IS NOT NULL AND length(trim(g.matched_entity_ids))>0
  AND g.source1_entity_id IN (SELECT entity_id FROM s1v)
""")

con.execute("""
CREATE TEMP TABLE tgt_gt AS
SELECT entity_id, country_norm cc, name_nosuffix tns, name_sorted tsrt, addr_norm tad
FROM (
  SELECT entity_id, country_norm, name_nosuffix, name_sorted, addr_norm FROM read_parquet('work/keys/train_s2.parquet')
  UNION ALL
  SELECT entity_id, country_norm, name_nosuffix, name_sorted, addr_norm FROM read_parquet('work/keys/train_s3.parquet')
) WHERE entity_id IN (SELECT mid FROM gtp)
""")

con.execute("""
CREATE TEMP TABLE pk AS
SELECT g.s1, g.mid, s.cc, s.sns, s.ssrt, s.sad, t.tns, t.tsrt, t.tad
FROM gtp g
JOIN s1v s ON g.s1=s.entity_id
JOIN tgt_gt t ON g.mid=t.entity_id
""")

con.execute("""
CREATE TEMP TABLE art AS
SELECT source1_entity_id s1, target_entity_id mid
FROM read_parquet('work/final_candidate_provenance.parquet')
""")

# Lost pairs
con.execute("""
CREATE TEMP TABLE lost_pk AS
SELECT p.*
FROM pk p
LEFT JOIN art a ON p.s1=a.s1 AND p.mid=a.mid
WHERE a.s1 IS NULL
""")

print("Lost pairs count:", con.sql("SELECT count(*) FROM lost_pk").fetchone()[0])

# To check if a lost pair has token overlap with DF <= 2000:
# Note: candidate_experiments.py dropped DF > 2000.
# Let's count shared tokens between s1 and mid in lost_pk
con.execute("""
CREATE TEMP TABLE lost_token_overlap AS
SELECT l.s1, l.mid, l.cc,
       len(list_intersect(str_split(l.sns, ' '), str_split(l.tns, ' '))) as name_tok_overlap,
       len(list_intersect(str_split(l.sad, ' '), str_split(l.tad, ' '))) as addr_tok_overlap
FROM lost_pk l
""")

r = con.sql("""
SELECT count(*) as total_lost,
       count(*) FILTER (WHERE name_tok_overlap > 0 AND addr_tok_overlap > 0) as both_toks,
       count(*) FILTER (WHERE name_tok_overlap > 0 AND addr_tok_overlap = 0) as name_tok_only,
       count(*) FILTER (WHERE name_tok_overlap = 0 AND addr_tok_overlap > 0) as addr_tok_only,
       count(*) FILTER (WHERE name_tok_overlap = 0 AND addr_tok_overlap = 0) as neither_tok
FROM lost_token_overlap
""").fetchone()

print("\n--- TOKEN OVERLAP BREAKDOWN OF ALL 105,856 LOST PAIRS ---")
print(f"Total lost: {r[0]}")
print(f"Both name and addr token overlap: {r[1]} ({100*r[1]/r[0]:.2f}%)")
print(f"Name token overlap ONLY: {r[2]} ({100*r[2]/r[0]:.2f}%)")
print(f"Addr token overlap ONLY: {r[3]} ({100*r[3]/r[0]:.2f}%)")
print(f"Neither token overlap (key-ineligible): {r[4]} ({100*r[4]/r[0]:.2f}%)")

con.close()
