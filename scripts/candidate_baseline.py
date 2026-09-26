"""Foundation item 5 — ONE simple reproducible baseline blocker + measurement.

Baseline rule (deliberately the simplest defensible blocker): a candidate (S1, S2/S3)
is generated iff they share the SAME (country_norm, name_nosuffix) key and the key is
non-empty. Measured on the held-out VAL split only (from er.split); training labels are
never consulted to build the blocker — GT is used ONLY to score recall afterwards.

Bounded-memory trick: candidate COUNT per S1 is computed by counting targets per key and
attributing that count to each S1 in the key — never materializing the (huge) candidate
cross-product for common names. Recall is computed by testing, for each GT val pair
(s1, mid), whether mid shares s1's key — also without building the candidate table.

Reproducible command:
    PYTHONUTF8=1 .venv/Scripts/python scripts/candidate_baseline.py --data-dir dataset
"""
import argparse
import os
import sys

import duckdb

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="dataset")
    ap.add_argument("--keys-dir", default="work/keys")
    ap.add_argument("--split", default="work/split_s1.parquet")
    args = ap.parse_args()
    gt = f"{args.data_dir}/train/train_ground_truth.tsv"
    k = args.keys_dir
    os.makedirs("work/duckdb_tmp", exist_ok=True)

    con = duckdb.connect()
    con.execute("SET memory_limit='512MB'; SET threads=4;")
    con.execute("SET temp_directory='work/duckdb_tmp'; SET preserve_insertion_order=false;")

    # val S1 keys
    con.execute(f"""CREATE TEMP TABLE s1v AS
        SELECT a.entity_id, a.country_norm, a.name_nosuffix
        FROM read_parquet('{k}/train_s1.parquet') a
        JOIN read_parquet('{args.split}') s
          ON a.entity_id=s.entity_id AND s.split='val'""")
    # target (S2+S3) keys and per-key target counts
    con.execute(f"""CREATE TEMP TABLE tkeys AS
        SELECT entity_id, country_norm, name_nosuffix FROM read_parquet('{k}/train_s2.parquet')
        UNION ALL
        SELECT entity_id, country_norm, name_nosuffix FROM read_parquet('{k}/train_s3.parquet')""")
    con.execute("""CREATE TEMP TABLE tkc AS
        SELECT country_norm, name_nosuffix, count(*) tc FROM tkeys
        WHERE length(name_nosuffix)>0 GROUP BY 1,2""")

    n_val = con.sql("SELECT count(*) FROM s1v").fetchone()[0]
    n_s2 = con.sql(f"SELECT count(*) FROM read_parquet('{k}/train_s2.parquet')").fetchone()[0]
    n_s3 = con.sql(f"SELECT count(*) FROM read_parquet('{k}/train_s3.parquet')").fetchone()[0]

    # per-S1 candidate size (0 for empty key or key absent from targets)
    con.execute("""CREATE TEMP TABLE cc AS
        SELECT s.entity_id s1,
               CASE WHEN length(s.name_nosuffix)=0 THEN 0 ELSE coalesce(t.tc,0) END AS c
        FROM s1v s LEFT JOIN tkc t
          ON s.country_norm=t.country_norm AND s.name_nosuffix=t.name_nosuffix""")
    stats = con.sql("""SELECT sum(c), avg(c),
        quantile_cont(c,0.5), quantile_cont(c,0.9), quantile_cont(c,0.95),
        quantile_cont(c,0.99), max(c), count(*) FILTER (WHERE c=0)
        FROM cc""").fetchone()
    total_cand, avg_c, med, p90, p95, p99, mx, zero_c = stats

    # GT val pairs
    con.execute(f"""CREATE TEMP TABLE gtp AS
        SELECT g.source1_entity_id s1, trim(x) mid
        FROM read_csv('{gt}', delim='\t', header=true, quote='', all_varchar=true) g,
             UNNEST(string_split(g.matched_entity_ids, ',')) AS u(x)
        WHERE g.matched_entity_ids IS NOT NULL AND length(trim(g.matched_entity_ids))>0
          AND g.source1_entity_id IN (SELECT entity_id FROM s1v)""")
    total_pairs = con.sql("SELECT count(*) FROM gtp").fetchone()[0]
    # covered iff target shares s1's (country,name_nosuffix), key non-empty
    covered = con.sql("""SELECT count(*) FROM gtp g
        JOIN s1v s ON g.s1=s.entity_id
        JOIN tkeys t ON g.mid=t.entity_id
        WHERE length(s.name_nosuffix)>0
          AND s.country_norm=t.country_norm AND s.name_nosuffix=t.name_nosuffix""").fetchone()[0]
    # val S1 with >=1 true match, to report S1-level recall too
    s1_any = con.sql("SELECT count(DISTINCT s1) FROM gtp").fetchone()[0]
    s1_cov = con.sql("""SELECT count(DISTINCT g.s1) FROM gtp g
        JOIN s1v s ON g.s1=s.entity_id JOIN tkeys t ON g.mid=t.entity_id
        WHERE length(s.name_nosuffix)>0
          AND s.country_norm=t.country_norm AND s.name_nosuffix=t.name_nosuffix""").fetchone()[0]

    brute = n_val * (n_s2 + n_s3)
    L = []
    p = L.append
    p("# Baseline blocker: exact (country_norm, name_nosuffix), VAL split\n")
    p(f"- val S1={n_val}, targets S2={n_s2} + S3={n_s3} = {n_s2+n_s3}")
    p(f"- total candidate pairs={total_cand}")
    p(f"- candidate size/S1: avg={avg_c:.2f} median={med:.0f} p90={p90:.0f} "
      f"p95={p95:.0f} p99={p99:.0f} max={mx:.0f}")
    p(f"- zero-candidate val S1={zero_c} ({100*zero_c/n_val:.2f}%)")
    p(f"- brute-force space (val S1 x targets)={brute}")
    p(f"- reduction ratio=1 - {total_cand}/{brute} = {1-total_cand/brute:.8f}")
    p(f"\n## Recall (GT pairs for val S1)\n")
    p(f"- total true val pairs={total_pairs}, covered={covered}, "
      f"true-pair recall={covered/total_pairs:.4f}")
    p(f"- lost GT links={total_pairs-covered}")
    p(f"- val S1 with >=1 true match={s1_any}; with >=1 covered={s1_cov} "
      f"(S1-level recall={s1_cov/s1_any:.4f})")

    # per-country true-pair recall (val is train-only: US/India) — isolates where the
    # exact-name key fails, e.g. India Devanagari<->Latin cross-script pairs.
    percc = con.sql("""SELECT s.country_norm,
          count(*) AS pairs,
          count(*) FILTER (WHERE length(s.name_nosuffix)>0
             AND s.country_norm=t.country_norm AND s.name_nosuffix=t.name_nosuffix) AS cov
        FROM gtp g JOIN s1v s ON g.s1=s.entity_id JOIN tkeys t ON g.mid=t.entity_id
        GROUP BY s.country_norm ORDER BY pairs DESC""").fetchall()
    pcl = "\n".join(f"  - {c}: recall={cov/pairs:.4f} ({cov}/{pairs})"
                    for c, pairs, cov in percc)
    p(f"\n## Per-country true-pair recall (baseline key)\n{pcl}")

    report = "\n".join(L) + "\n"
    with open("work/baseline_blocking.md", "w", encoding="utf-8") as f:
        f.write(report)
    print(report)

    # ---- diagnostic headroom: word-order-invariant key (name_sorted), same cost ----
    # Not a new pipeline — reuses an already-materialized key to quantify how much
    # recall a trivial change (sort tokens) buys, to prioritise the next experiment.
    con.execute(f"""CREATE TEMP TABLE s1v2 AS
        SELECT a.entity_id, a.country_norm, a.name_sorted
        FROM read_parquet('{k}/train_s1.parquet') a
        JOIN read_parquet('{args.split}') s
          ON a.entity_id=s.entity_id AND s.split='val'""")
    con.execute(f"""CREATE TEMP TABLE tkeys2 AS
        SELECT entity_id, country_norm, name_sorted FROM read_parquet('{k}/train_s2.parquet')
        UNION ALL
        SELECT entity_id, country_norm, name_sorted FROM read_parquet('{k}/train_s3.parquet')""")
    con.execute("""CREATE TEMP TABLE tkc2 AS
        SELECT country_norm, name_sorted, count(*) tc FROM tkeys2
        WHERE length(name_sorted)>0 GROUP BY 1,2""")
    tot2 = con.sql("""SELECT coalesce(sum(CASE WHEN length(s.name_sorted)=0 THEN 0
                        ELSE coalesce(t.tc,0) END),0)
        FROM s1v2 s LEFT JOIN tkc2 t
          ON s.country_norm=t.country_norm AND s.name_sorted=t.name_sorted""").fetchone()[0]
    cov2 = con.sql("""SELECT count(*) FROM gtp g
        JOIN s1v2 s ON g.s1=s.entity_id
        JOIN tkeys2 t ON g.mid=t.entity_id
        WHERE length(s.name_sorted)>0
          AND s.country_norm=t.country_norm AND s.name_sorted=t.name_sorted""").fetchone()[0]
    diag = (f"\n## Diagnostic — (country_norm, name_sorted) word-order-invariant key\n"
            f"- true-pair recall={cov2/total_pairs:.4f} (vs {covered/total_pairs:.4f} baseline), "
            f"total candidate pairs={tot2} (vs {total_cand})\n")
    with open("work/baseline_blocking.md", "a", encoding="utf-8") as f:
        f.write(diag)
    print(diag)
    print("Peak DuckDB memory:",
          con.sql("SELECT max(memory_usage_bytes) FROM duckdb_memory()").fetchone()[0])


if __name__ == "__main__":
    main()
