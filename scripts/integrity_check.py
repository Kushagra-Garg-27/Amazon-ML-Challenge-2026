"""Foundation item 1 — dataset & ground-truth integrity, bounded-memory (DuckDB).

Reproducible command:
    PYTHONUTF8=1 .venv/Scripts/python scripts/integrity_check.py --data-dir dataset

Everything runs under a hard 512 MB DuckDB memory limit (spills to work/duckdb_tmp),
proving the checks are out-of-core. No pandas, no Cartesian join. Reads only the TSVs.
Writes a compact report to work/integrity_report.md and prints it.
"""
import argparse
import os
import sys

import duckdb

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

R = "read_csv('{p}', delim='\t', header=true, quote='', all_varchar=true)"


def one(con, sql):
    return con.sql(sql).fetchone()


def allrows(con, sql):
    return con.sql(sql).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="dataset")
    args = ap.parse_args()
    d = args.data_dir
    os.makedirs("work/duckdb_tmp", exist_ok=True)

    con = duckdb.connect()
    con.execute("SET memory_limit='512MB'; SET threads=4;")
    con.execute("SET temp_directory='work/duckdb_tmp'; SET preserve_insertion_order=false;")

    sources = {
        "train_s1": f"{d}/train/train_source1.tsv",
        "train_s2": f"{d}/train/train_source2.tsv",
        "train_s3": f"{d}/train/train_source3.tsv",
        "test_s1": f"{d}/test/test_source1.tsv",
        "test_s2": f"{d}/test/test_source2.tsv",
        "test_s3": f"{d}/test/test_source3.tsv",
    }
    gt = f"{d}/train/train_ground_truth.tsv"
    L = []
    p = L.append
    p("# Dataset & GT Integrity Report (bounded-memory, DuckDB memory_limit=512MB)\n")

    # ---- per-source profile ----
    p("## Per-source profile\n")
    p("| file | rows | distinct id | id unique? | empty name | empty addr | #countries |")
    p("|---|---|---|---|---|---|---|")
    for key, path in sources.items():
        r = one(con, f"""
            SELECT count(*), count(DISTINCT entity_id),
              sum(CASE WHEN business_name IS NULL OR length(trim(business_name))=0 THEN 1 ELSE 0 END),
              sum(CASE WHEN business_address IS NULL OR length(trim(business_address))=0 THEN 1 ELSE 0 END),
              count(DISTINCT country)
            FROM {R.format(p=path)}""")
        rows, uniq, en, ea, nc = r
        p(f"| {key} | {rows} | {uniq} | {'YES' if rows==uniq else 'NO'} | {en} | {ea} | {nc} |")
    p("")

    # ---- country distribution ----
    p("## Country distribution (per file)\n")
    for key, path in sources.items():
        dist = allrows(con, f"SELECT country, count(*) c FROM {R.format(p=path)} "
                            f"GROUP BY country ORDER BY c DESC")
        shown = ", ".join(f"{c}={n}" for c, n in dist[:6])
        p(f"- **{key}**: {shown}")
    p("")

    # ---- id tables for referential checks (entity_id only; small) ----
    for t, path in (("ids_s1", sources["train_s1"]),
                    ("ids_s2", sources["train_s2"]),
                    ("ids_s3", sources["train_s3"])):
        con.execute(f"CREATE OR REPLACE TEMP TABLE {t} AS "
                    f"SELECT entity_id FROM {R.format(p=path)}")

    # ---- GT profile ----
    p("## Ground truth\n")
    gt_rows, gt_uniq, gt_empty = one(con, f"""
        SELECT count(*), count(DISTINCT source1_entity_id),
          sum(CASE WHEN matched_entity_ids IS NULL OR length(trim(matched_entity_ids))=0
                   THEN 1 ELSE 0 END)
        FROM {R.format(p=gt)}""")
    p(f"- rows={gt_rows}, distinct S1={gt_uniq}, duplicate-S1 rows={gt_rows-gt_uniq}, "
      f"empty(singleton) rows={gt_empty}")

    hist = allrows(con, f"""
        SELECT CASE WHEN matched_entity_ids IS NULL OR length(trim(matched_entity_ids))=0
                    THEN 0 ELSE length(string_split(matched_entity_ids, ',')) END AS n,
               count(*) c
        FROM {R.format(p=gt)} GROUP BY n ORDER BY n""")
    total_pairs = sum(n * c for n, c in hist)
    binned = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5+": 0}
    mx = 0
    for n, c in hist:
        mx = max(mx, n)
        binned["5+" if n >= 5 else str(n)] += c
    p(f"- total matched pairs={total_pairs}, avg matches/S1={total_pairs/gt_rows:.3f}, "
      f"max matches={mx}")
    p(f"- match-count dist: " + ", ".join(
        f"{k}={binned[k]} ({100*binned[k]/gt_rows:.1f}%)" for k in binned))

    # explode to pairs (bounded; spills)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE gt_pairs AS
        SELECT source1_entity_id AS s1, trim(x) AS mid
        FROM {R.format(p=gt)}, UNNEST(string_split(matched_entity_ids, ',')) AS t(x)
        WHERE matched_entity_ids IS NOT NULL AND length(trim(matched_entity_ids))>0""")
    pref = allrows(con, "SELECT substr(mid,1,3) pfx, count(*) c FROM gt_pairs "
                        "GROUP BY pfx ORDER BY c DESC")
    p("- matched-id prefix breakdown: " + ", ".join(f"{k}={v}" for k, v in pref))

    dup_lists = one(con, """
        SELECT count(*) FROM (
          SELECT s1 FROM gt_pairs GROUP BY s1, mid HAVING count(*)>1)""")[0]
    p(f"- S1 rows containing a duplicate id within their own list: {dup_lists}")

    # ---- referential integrity ----
    p("\n## Referential integrity (GT ids exist in training sources?)\n")
    miss_s1 = one(con, f"""SELECT count(*) FROM (SELECT DISTINCT source1_entity_id s1
        FROM {R.format(p=gt)}) g LEFT JOIN ids_s1 i ON g.s1=i.entity_id
        WHERE i.entity_id IS NULL""")[0]
    miss_s2 = one(con, """SELECT count(*) FROM (SELECT DISTINCT mid FROM gt_pairs
        WHERE mid LIKE 'S2-%') g LEFT JOIN ids_s2 i ON g.mid=i.entity_id
        WHERE i.entity_id IS NULL""")[0]
    miss_s3 = one(con, """SELECT count(*) FROM (SELECT DISTINCT mid FROM gt_pairs
        WHERE mid LIKE 'S3-%') g LEFT JOIN ids_s3 i ON g.mid=i.entity_id
        WHERE i.entity_id IS NULL""")[0]
    p(f"- GT S1 ids missing from train_source1: {miss_s1}")
    p(f"- GT S2 matched-ids missing from train_source2: {miss_s2}")
    p(f"- GT S3 matched-ids missing from train_source3: {miss_s3}")

    # ---- cross-file uniqueness & leakage-relevant sharing ----
    p("\n## Cross-file uniqueness & match sharing\n")
    overlap = one(con, "SELECT count(*) FROM ids_s2 a JOIN ids_s3 b USING(entity_id)")[0]
    p(f"- train S2∩S3 entity_id value overlap: {overlap}")
    multi = one(con, """SELECT count(*) FROM (
        SELECT mid FROM gt_pairs GROUP BY mid HAVING count(DISTINCT s1)>1)""")[0]
    p(f"- matched ids assigned to >1 distinct S1 (cross-S1 sharing / leakage risk): {multi}")

    report = "\n".join(L) + "\n"
    with open("work/integrity_report.md", "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print("Peak DuckDB memory this session:",
          con.sql("SELECT max(memory_usage_bytes) FROM duckdb_memory()").fetchone()[0])


if __name__ == "__main__":
    main()
