"""Audit capping loss and provenance breakdown for Candidate Refinement Report.
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

print("Loading artifact...")
con.execute("""
CREATE TEMP TABLE art AS
SELECT source1_entity_id s1, target_entity_id mid, prov
FROM read_parquet('work/final_candidate_provenance.parquet')
""")

tot = con.sql("SELECT count(*) FROM pk").fetchone()[0]
nval = con.sql("SELECT count(*) FROM s1v").fetchone()[0]
print(f"Total GT links: {tot}, S1 in val: {nval}")

# Check artifact duplicates and unique pairs
art_rows = con.sql("SELECT count(*) FROM art").fetchone()[0]
art_unique_pairs = con.sql("SELECT count(DISTINCT (s1, mid)) FROM art").fetchone()[0]
print(f"Artifact rows: {art_rows}, Distinct pairs: {art_unique_pairs}")

# Provenance bitmask breakdown
print("\n--- PROVENANCE BITMASK BREAKDOWN ---")
# prov bitmask: 1=sorted, 2=addr, 4=nametok, 8=addrtok
prov_df = con.sql("""
SELECT prov,
       count(*) as n_cands,
       count(p.mid) as n_gt_hits
FROM art a
LEFT JOIN pk p ON a.s1=p.s1 AND a.mid=p.mid
GROUP BY 1 ORDER BY 1
""").fetchall()

print("| prov | bits (8 4 2 1) | meaning | candidates | % cands | GT hits | % GT hits | precision |")
print("|------|----------------|---------|------------|---------|---------|-----------|-----------|")
names = {
    1: "only sorted",
    2: "only exact_addr",
    3: "sorted + addr",
    4: "only nametok",
    5: "sorted + nametok",
    6: "addr + nametok",
    7: "sorted + addr + nametok",
    8: "only addrtok",
    9: "sorted + addrtok",
    10: "addr + addrtok",
    11: "sorted + addr + addrtok",
    12: "nametok + addrtok",
    13: "sorted + nametok + addrtok",
    14: "addr + nametok + addrtok",
    15: "all 4 passes"
}
for r in prov_df:
    prv, nc, ng = r
    meaning = names.get(prv, "unknown")
    bits = f"{(prv>>3)&1} {(prv>>2)&1} {(prv>>1)&1} {prv&1}"
    prec = ng / nc if nc > 0 else 0
    print(f"| {prv:4d} | {bits} | {meaning:26s} | {nc:10,d} | {100*nc/art_rows:6.2f}% | {ng:7,d} | {100*ng/tot:6.2f}% | {prec*100:6.3f}% |")

# S1 coverage & Target coverage
print("\n--- S1 AND TARGET RECALL ---")
art_s1_hits = con.sql("SELECT count(DISTINCT p.s1) FROM pk p JOIN art a ON p.s1=a.s1 AND p.mid=a.mid").fetchone()[0]
tot_s1_with_gt = con.sql("SELECT count(DISTINCT s1) FROM pk").fetchone()[0]
art_tgt_hits = con.sql("SELECT count(DISTINCT p.mid) FROM pk p JOIN art a ON p.s1=a.s1 AND p.mid=a.mid").fetchone()[0]
tot_tgt_in_gt = con.sql("SELECT count(DISTINCT mid) FROM pk").fetchone()[0]

print(f"S1 entities with >=1 GT link: {tot_s1_with_gt}")
print(f"S1 entities with >=1 GT link recovered: {art_s1_hits} ({art_s1_hits/tot_s1_with_gt:.4f} or {art_s1_hits/nval:.4f} of all val S1)")
print(f"Distinct targets in GT: {tot_tgt_in_gt}")
print(f"Distinct true targets recovered: {art_tgt_hits} ({art_tgt_hits/tot_tgt_in_gt:.4f})")

# Sister target analysis
print("\n--- SISTER TARGET RECOVERY (S2 vs S3) ---")
# Each S1 in GT can have links to S2, S3, or both.
con.execute("""
CREATE TEMP TABLE s1_gt_targets AS
SELECT s1,
       max(CASE WHEN mid LIKE 'S2-%' THEN mid ELSE NULL END) as s2_gt,
       max(CASE WHEN mid LIKE 'S3-%' THEN mid ELSE NULL END) as s3_gt
FROM pk GROUP BY s1
""")

con.execute("""
CREATE TEMP TABLE s1_art_hits AS
SELECT p.s1,
       max(CASE WHEN p.mid LIKE 'S2-%' THEN 1 ELSE 0 END) as hit_s2,
       max(CASE WHEN p.mid LIKE 'S3-%' THEN 1 ELSE 0 END) as hit_s3
FROM pk p
JOIN art a ON p.s1=a.s1 AND p.mid=a.mid
GROUP BY p.s1
""")

con.execute("""
CREATE TEMP TABLE s1_target_status AS
SELECT g.s1,
       CASE WHEN g.s2_gt IS NOT NULL THEN 1 ELSE 0 END has_s2_gt,
       CASE WHEN g.s3_gt IS NOT NULL THEN 1 ELSE 0 END has_s3_gt,
       coalesce(h.hit_s2, 0) hit_s2,
       coalesce(h.hit_s3, 0) hit_s3
FROM s1_gt_targets g
LEFT JOIN s1_art_hits h ON g.s1=h.s1
""")

r_both = con.sql("""
SELECT count(*) as total_both_gt,
       count(*) FILTER (WHERE hit_s2=1 AND hit_s3=1) as both_recovered,
       count(*) FILTER (WHERE hit_s2=1 AND hit_s3=0) as only_s2_recovered,
       count(*) FILTER (WHERE hit_s2=0 AND hit_s3=1) as only_s3_recovered,
       count(*) FILTER (WHERE hit_s2=0 AND hit_s3=0) as neither_recovered
FROM s1_target_status
WHERE has_s2_gt=1 AND has_s3_gt=1
""").fetchone()

print(f"S1 with BOTH S2 and S3 in GT: {r_both[0]:,}")
print(f"  Both recovered: {r_both[1]:,} ({100*r_both[1]/r_both[0]:.2f}%)")
print(f"  Only S2 recovered (S3 lost): {r_both[2]:,} ({100*r_both[2]/r_both[0]:.2f}%)")
print(f"  Only S3 recovered (S2 lost): {r_both[3]:,} ({100*r_both[3]/r_both[0]:.2f}%)")
print(f"  Neither recovered: {r_both[4]:,} ({100*r_both[4]/r_both[0]:.2f}%)")

# Crowding analysis in the 61,897 capping loss links
# Which pass generated them? And S2 vs S3?
con.execute("""
CREATE TEMP TABLE lost_pk AS
SELECT p.*,
       CASE WHEN p.mid LIKE 'S2-%' THEN 'S2' ELSE 'S3' END mid_src
FROM pk p
LEFT JOIN art a ON p.s1=a.s1 AND p.mid=a.mid
WHERE a.s1 IS NULL
""")

lost_n = con.sql("SELECT count(*) FROM lost_pk").fetchone()[0]
print(f"\nTotal lost GT links: {lost_n}")
s2_lost = con.sql("SELECT count(*) FROM lost_pk WHERE mid_src='S2'").fetchone()[0]
s3_lost = con.sql("SELECT count(*) FROM lost_pk WHERE mid_src='S3'").fetchone()[0]
print(f"Lost S2 targets: {s2_lost} ({100*s2_lost/lost_n:.2f}%)")
print(f"Lost S3 targets: {s3_lost} ({100*s3_lost/lost_n:.2f}%)")

# For how many lost links did the S1 entity ALREADY have its sister target found?
sister_found = con.sql("""
SELECT count(*) FROM lost_pk l
JOIN s1_target_status st ON l.s1=st.s1
WHERE (l.mid_src='S2' AND st.hit_s3=1) OR (l.mid_src='S3' AND st.hit_s2=1)
""").fetchone()[0]
print(f"Lost links where sister target WAS recovered: {sister_found} ({100*sister_found/lost_n:.2f}% of all lost links)")

con.close()
