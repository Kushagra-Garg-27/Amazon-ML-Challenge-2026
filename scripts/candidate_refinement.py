"""Candidate-generation refinement -- phased, memory-safe.

Phase 1: Reconcile metrics + audit artifact + loss decomposition.
Phase 2: Policy comparisons (single connection, infra built once).
Phase 3: Heavy blocks + Pareto frontier.

Run:
  PYTHONUTF8=1 .venv/Scripts/python scripts/candidate_refinement.py --data-dir dataset
"""
import argparse
import gc
import os
import sys
import time

import duckdb

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

L = []


def p(s=""):
    L.append(s)
    print(s, flush=True)


def fresh_con(mem_mb=900):
    """New DuckDB connection. mem_mb controls memory limit."""
    c = duckdb.connect()
    c.execute(f"SET memory_limit='{mem_mb}MB'; SET threads=1;")
    c.execute("SET temp_directory='work/duckdb_tmp'; SET preserve_insertion_order=false;")
    c.execute("""CREATE MACRO jac(a,b) AS (
        len(list_intersect(str_split(a,' '), str_split(b,' ')))::DOUBLE
        / nullif(len(list_distinct(list_concat(str_split(a,' '), str_split(b,' ')))),0))""")
    c.execute("""CREATE MACRO scr(x) AS (
        CASE WHEN regexp_matches(x,'[\\x{0900}-\\x{097F}]') AND regexp_matches(x,'[a-z]') THEN 'mixed'
             WHEN regexp_matches(x,'[\\x{0900}-\\x{097F}]') THEN 'deva'
             WHEN regexp_matches(x,'[a-z]') THEN 'latin' ELSE 'other' END)""")
    return c


def load_val_s1(con, k, split):
    """Load val S1 only."""
    con.execute(f"""CREATE TEMP TABLE s1v AS
        SELECT a.entity_id, a.country_norm cc, a.name_nosuffix sns, a.name_sorted ssrt,
               a.addr_norm sad
        FROM read_parquet('{k}/train_s1.parquet') a
        JOIN read_parquet('{split}') s ON a.entity_id=s.entity_id AND s.split='val'""")


def load_pk(con, k, split, gt):
    """Load val S1, targets, and GT-pair table. Returns (tot, nS1pairs, nval)."""
    load_val_s1(con, k, split)
    con.execute(f"""CREATE TEMP TABLE tgt AS
        SELECT entity_id, country_norm cc, name_nosuffix tns, name_sorted tsrt, addr_norm tad
          FROM read_parquet('{k}/train_s2.parquet')
        UNION ALL
        SELECT entity_id, country_norm, name_nosuffix, name_sorted, addr_norm
          FROM read_parquet('{k}/train_s3.parquet')""")
    con.execute(f"""CREATE TEMP TABLE gtp AS
        SELECT g.source1_entity_id s1, trim(x) mid
        FROM read_csv('{gt}', delim='\\t', header=true, quote='', all_varchar=true) g,
             UNNEST(string_split(g.matched_entity_ids, ',')) AS u(x)
        WHERE g.matched_entity_ids IS NOT NULL AND length(trim(g.matched_entity_ids))>0
          AND g.source1_entity_id IN (SELECT entity_id FROM s1v)""")
    con.execute("""CREATE TEMP TABLE pk AS
        SELECT g.s1, g.mid, s.cc, s.sns, s.ssrt, s.sad, t.tns, t.tsrt, t.tad
        FROM gtp g JOIN s1v s ON g.s1=s.entity_id JOIN tgt t ON g.mid=t.entity_id""")
    tot = con.sql("SELECT count(*) FROM pk").fetchone()[0]
    nS1pairs = con.sql("SELECT count(DISTINCT s1) FROM pk").fetchone()[0]
    nval = con.sql("SELECT count(*) FROM s1v").fetchone()[0]
    return tot, nS1pairs, nval


# ============================================================
# PHASE 1: Reconcile + Audit + Loss Decomposition
# ============================================================
def phase1(args):
    p("# Candidate-Generation Refinement Report")
    p("")

    gt = f"{args.data_dir}/train/train_ground_truth.tsv"
    art = "work/final_candidate_provenance.parquet"
    art_u = art.replace(os.sep, '/')

    con = fresh_con()
    tot, nS1pairs, nval = load_pk(con, args.keys_dir, args.split, gt)
    p(f"Base: val S1={nval}; val S1 with >=1 GT link={nS1pairs}; total GT links={tot}")

    # Load the artifact into a temp table once -- all subsequent joins are table-to-table
    p("\n--- Loading artifact into temp table ---")
    con.execute(f"""CREATE TEMP TABLE art_val AS
        SELECT source1_entity_id s1, target_entity_id mid, prov
        FROM read_parquet('{art_u}')""")
    art_val_n = con.sql("SELECT count(*) FROM art_val").fetchone()[0]
    p(f"  Loaded {art_val_n:,} rows")

    # Full artifact stats
    art_n = art_val_n
    art_dup = art_n - con.sql("SELECT count(DISTINCT (s1, mid)) FROM art_val").fetchone()[0]
    art_size = os.path.getsize(art)

    # Join-based metrics (all use temp table, no parquet re-scan)
    art_cov = con.sql("""SELECT count(*) FROM pk p
        INNER JOIN art_val a ON p.s1=a.s1 AND p.mid=a.mid""").fetchone()[0]
    art_s1 = con.sql("""SELECT count(DISTINCT p.s1) FROM pk p
        INNER JOIN art_val a ON p.s1=a.s1 AND p.mid=a.mid""").fetchone()[0]
    art_dist = con.sql("""SELECT avg(c),median(c),quantile_cont(c,0.9),quantile_cont(c,0.95),
        quantile_cont(c,0.99),max(c) FROM
        (SELECT s.entity_id, count(a.mid) c FROM s1v s
         LEFT JOIN art_val a ON s.entity_id=a.s1 GROUP BY 1)""").fetchone()

    # Oracle = established from candidate_experiments.py step 6
    oracle_cov = 719960
    oracle_recall = oracle_cov / tot

    # ---- SECTION 1: RECONCILE ----
    p("\n" + "=" * 70)
    p("SECTION 1: RECONCILE EVERY REPORTED OPERATING POINT")
    p("=" * 70)

    p("\n### Row definitions")
    p("  A = KEY-ELIGIBILITY ORACLE: GT pair shares >=1 eligible key. NOT materialized.")
    p("  B = ACTUAL MATERIALIZED ARTIFACT: post-ranking/post-cap pairs on disk.")
    p("")

    p("### A. KEY-ELIGIBILITY ORACLE")
    p("  Eligible-key passes: sorted_name U exact_addr U nametok(DF<=2000) U addrtok(DF<=2000)")
    p("  Candidates materialized: NO")
    p(f"  GT links recovered: {oracle_cov}")
    p(f"  Pair-level GT recall: {oracle_recall:.4f}")
    p("  Candidate count: NOT MEASURED")
    p("  Resource cost: NOT MEASURED")

    p("\n### B. ACTUAL MATERIALIZED CANDIDATE ARTIFACT")
    p("  Post-ranking/post-cap union of sorted+addr+nametok(top100)+addrtok(top100)")
    p("  Candidates materialized: YES")
    p(f"  Distinct candidate count: {art_n:,}")
    p(f"  Duplicate identities: {art_dup}")
    p(f"  GT links recovered: {art_cov:,}")
    p(f"  Pair-level GT recall: {art_cov/tot:.4f}")
    p(f"  S1 coverage: {art_s1/nS1pairs:.4f}")
    p(f"  Mean/Median/P90/P95/P99/Max cand/S1: "
      f"{art_dist[0]:.2f}/{art_dist[1]:.0f}/{art_dist[2]:.0f}/"
      f"{art_dist[3]:.0f}/{art_dist[4]:.0f}/{art_dist[5]:.0f}")
    p(f"  Artifact size: {art_size:,} bytes ({art_size/1e6:.1f} MB)")
    p("  Wall time: ~10 min (from original run)")
    p("  Process peak RSS: ~1.27 GiB (from measure_peak.py)")

    p("\n### RECONCILIATION")
    p(f"  0.9425 = KEY-ELIGIBILITY ORACLE ({oracle_cov}/{tot}). NOT materialized.")
    p(f"  0.8614 = MATERIALIZED ARTIFACT ({art_cov}/{tot}). Per-pass cap causes {oracle_cov - art_cov} loss.")
    p("  0.9416 = GROUPED KEY-ELIGIBILITY ORACLE (different S1 partition, same type as 0.9425)")

    p("\n### PER-COUNTRY (materialized artifact)")
    for cc, tt, mc in con.sql("""
        SELECT p.cc, count(*),
               count(*) FILTER(WHERE a.s1 IS NOT NULL)
        FROM pk p LEFT JOIN art_val a ON p.s1=a.s1 AND p.mid=a.mid
        GROUP BY 1 ORDER BY 2 DESC""").fetchall():
        p(f"  {cc}: total GT={tt}, materialized={mc} ({mc/tt:.4f})")

    # ---- SECTION 2: VERIFY ARTIFACT ----
    p("\n" + "=" * 70)
    p("SECTION 2: VERIFY THE FINAL ARTIFACT DIRECTLY")
    p("=" * 70)
    p(f"\n  Total rows: {art_n:,}")
    p(f"  Distinct (S1, target): {art_n - art_dup:,}")
    p(f"  Duplicates: {art_dup}")
    p(f"  GT recovered by direct join: {art_cov:,} {'OK' if art_cov == 658063 else 'MISMATCH'}")
    p(f"  GT missing: {tot - art_cov:,} {'OK' if (tot - art_cov) == 105856 else 'MISMATCH'}")

    prov = con.sql("SELECT min(prov), max(prov), count(DISTINCT prov), count(*) FILTER(WHERE prov<1 OR prov>15) FROM art_val").fetchone()
    p(f"  Provenance: min={prov[0]}, max={prov[1]}, distinct={prov[2]}, invalid={prov[3]}")
    p(f"  Parquet file: {art_size:,} bytes")
    rg = con.sql(f"SELECT count(*) FROM parquet_metadata('{art_u}')").fetchone()[0]
    p(f"  Row groups: {rg}")

    s2c = con.sql("SELECT count(*) FROM art_val WHERE mid LIKE 'S2-%'").fetchone()[0]
    s3c = con.sql("SELECT count(*) FROM art_val WHERE mid LIKE 'S3-%'").fetchone()[0]
    p(f"\n  S2 candidates: {s2c:,} ({s2c/art_n:.4f})")
    p(f"  S3 candidates: {s3c:,} ({s3c/art_n:.4f})")

    gt_s2 = con.sql("SELECT count(*) FROM pk WHERE mid LIKE 'S2-%'").fetchone()[0]
    gt_s3 = con.sql("SELECT count(*) FROM pk WHERE mid LIKE 'S3-%'").fetchone()[0]
    rec_s2 = con.sql("""SELECT count(*) FROM pk p
        INNER JOIN art_val a ON p.s1=a.s1 AND p.mid=a.mid WHERE p.mid LIKE 'S2-%'""").fetchone()[0]
    rec_s3 = con.sql("""SELECT count(*) FROM pk p
        INNER JOIN art_val a ON p.s1=a.s1 AND p.mid=a.mid WHERE p.mid LIKE 'S3-%'""").fetchone()[0]
    p(f"  S2 recall: {rec_s2}/{gt_s2} ({rec_s2/gt_s2:.4f}), lost={gt_s2-rec_s2}")
    p(f"  S3 recall: {rec_s3}/{gt_s3} ({rec_s3/gt_s3:.4f}), lost={gt_s3-rec_s3}")
    s2lr, s3lr = (gt_s2-rec_s2)/gt_s2, (gt_s3-rec_s3)/gt_s3
    p(f"  {'DISPROPORTIONATE' if max(s2lr,s3lr)>1.5*min(s2lr,s3lr) else 'Balanced'}: S2 loss={s2lr:.4f}, S3 loss={s3lr:.4f}")

    # ---- SECTION 3: LOSS DECOMPOSITION ----
    p("\n" + "=" * 70)
    p("SECTION 3: SPLIT THE LOST LINKS")
    p("=" * 70)

    total_lost = tot - art_cov
    key_ineligible = tot - oracle_cov
    cap_loss = oracle_cov - art_cov

    p(f"\n  Total lost: {total_lost}")
    p(f"  A. KEY-INELIGIBLE: {key_ineligible} {'OK' if key_ineligible == 43959 else 'MISMATCH'}")
    p(f"  B. CAP/RANKING LOSS: {cap_loss} {'OK' if cap_loss == 61897 else 'MISMATCH'}")
    p(f"  Sum={key_ineligible+cap_loss} Mutually exclusive: {'YES' if key_ineligible+cap_loss==total_lost else 'NO'}")

    # Build lost-pairs table (LEFT JOIN anti-join; uses temp table, not parquet)
    con.execute("""CREATE TEMP TABLE cap_loss_pairs AS
        SELECT p.s1, p.mid, p.cc, p.sns, p.ssrt, p.sad, p.tns, p.tsrt, p.tad
        FROM pk p
        LEFT JOIN art_val a ON p.s1=a.s1 AND p.mid=a.mid
        WHERE a.s1 IS NULL""")

    cl_total = con.sql("SELECT count(*) FROM cap_loss_pairs").fetchone()[0]

    cl_sorted_or_addr = con.sql("""SELECT count(*) FROM cap_loss_pairs
        WHERE (length(ssrt)>0 AND ssrt=tsrt) OR (length(sad)>0 AND sad=tad)""").fetchone()[0]
    p(f"\n  Lost pairs with sorted/addr coverage (should be 0): {cl_sorted_or_addr}")

    p(f"\n### CAP/RANKING LOSS characteristics (all {cl_total} lost pairs)")
    p("  Note: split between token-eligible and truly-ineligible is verified by count only.")
    p(f"  Key-ineligible count: {key_ineligible} (from oracle)")
    p(f"  Cap/ranking loss count: {cap_loss} (from oracle)")

    for cc, n in con.sql("SELECT mid LIKE 'S2-%', count(*) FROM cap_loss_pairs GROUP BY 1").fetchall():
        label = "S2" if cc else "S3"
        p(f"  {label}: {n} ({100*n/cl_total:.1f}%)")
    for cc, n in con.sql("SELECT cc, count(*) FROM cap_loss_pairs GROUP BY 1 ORDER BY 1").fetchall():
        p(f"  {cc}: {n} ({100*n/cl_total:.1f}%)")
    both_addr = con.sql("SELECT count(*) FROM cap_loss_pairs WHERE length(sad)>0 AND length(tad)>0").fetchone()[0]
    p(f"  both addr present: {both_addr} ({100*both_addr/cl_total:.1f}%)")
    p(f"  missing addr: {cl_total-both_addr} ({100*(cl_total-both_addr)/cl_total:.1f}%)")

    # ---- SECTION 9: LOST-PAIR DIAGNOSTICS ----
    p("\n" + "=" * 70)
    p("SECTION 9: LOST-PAIR REPORTING (NON-EXCLUSIVE DIAGNOSTICS)")
    p("=" * 70)

    p(f"\n### A. Mutually exclusive partition: ineligible={key_ineligible}, cap={cap_loss}")

    p(f"\n### B. Non-exclusive diagnostics on ALL {cl_total} lost pairs")
    p("  Categories overlap.")
    diags = [
        ("zero name overlap", "jac(sns,tns)=0 OR jac(sns,tns) IS NULL"),
        ("low name overlap (0,0.5)", "jac(sns,tns)>0 AND jac(sns,tns)<0.5"),
        ("moderate name [0.5,1)", "jac(sns,tns)>=0.5 AND jac(sns,tns)<1.0"),
        ("exact name", "jac(sns,tns)>=1.0"),
        ("missing address", "length(sad)=0 OR length(tad)=0"),
        ("addr jac [0.5,1)", "jac(sad,tad)>=0.5 AND jac(sad,tad)<1.0 AND length(sad)>0 AND length(tad)>0"),
        ("addr jac <0.5", "coalesce(jac(sad,tad),0)<0.5 AND length(sad)>0 AND length(tad)>0"),
        ("cross-script", "(scr(sns)='deva' AND scr(tns)='latin') OR (scr(sns)='latin' AND scr(tns)='deva')"),
        ("country=us", "cc='us'"),
        ("country=india", "cc='india'"),
        ("source=S2", "mid LIKE 'S2-%'"),
        ("source=S3", "mid LIKE 'S3-%'"),
    ]
    p("  | Category | Count | % |")
    p("  |----------|-------|---|")
    for cat, cond in diags:
        try:
            n = con.sql(f"SELECT count(*) FROM cap_loss_pairs WHERE {cond}").fetchone()[0]
            p(f"  | {cat} | {n} | {100*n/cl_total:.1f}% |")
        except Exception as e:
            p(f"  | {cat} | ERR: {e} |")

    # ---- SECTION 8: GROUPED HOLDOUT ----
    p("\n" + "=" * 70)
    p("SECTION 8: GROUPED HOLDOUT INTERPRETATION")
    p("=" * 70)
    p("\n  The grouped split holds out S1 entities whose (country, name_sorted) key")
    p("  is never seen in training. Blocking is deterministic and token DFs come from")
    p("  the full target set -- so the grouped split tests the distribution of")
    p("  key-eligibility recall, not blocking generalization.")
    p("  It is NOT a candidate-generalization test.\n")
    p("  0.9416 = KEY-ELIGIBILITY ORACLE on the grouped S1 partition.")
    p("  Same type as the 0.9425 development oracle. NOT a materialized artifact.")
    p("  Candidate count: NOT MEASURED. Tail distribution: NOT MEASURED.")
    sp = "work/split_s1_grouped.parquet"
    if os.path.exists(sp):
        gn = con.sql(f"""SELECT count(*) FROM (
            SELECT g.source1_entity_id s1, trim(x) mid
            FROM read_csv('{gt}', delim='\\t', header=true, quote='', all_varchar=true) g,
                 UNNEST(string_split(g.matched_entity_ids, ',')) AS u(x)
            WHERE g.matched_entity_ids IS NOT NULL AND length(trim(g.matched_entity_ids))>0
              AND g.source1_entity_id IN (
                  SELECT entity_id FROM read_parquet('{sp}') WHERE split='val'))""").fetchone()[0]
        p(f"  Grouped val GT links: {gn}")
        p(f"  0.9416 x {gn} = {int(0.9416 * gn)} (reported 728594)")

    # ---- SECTION 7: RESOURCE PROJECTION ----
    p("\n" + "=" * 70)
    p("SECTION 7: FULL-SCALE RESOURCE PROJECTION")
    p("=" * 70)
    bpc = art_size / art_n
    mean_cps = art_n / nval
    p(f"\n  Measured basis: {art_n:,} candidates, {art_size:,} bytes, {bpc:.1f} bytes/cand, {mean_cps:.1f} cand/S1")
    for label, n_s1 in [("Full train (2206821 S1)", 2206821), ("Full test (1732544 S1)", 1732544)]:
        pc = int(n_s1 * mean_cps)
        pa = pc * bpc
        pf = pc * 200
        p(f"\n  {label}:")
        p(f"    Projected candidates: {pc:,}")
        p(f"    Provenance artifact: {pa/1e9:.2f} GB")
        p(f"    Feature artifact (~50 features): {pf/1e9:.2f} GB")
        p(f"    Temp disk: ~{pa/1e9*3:.1f} GB")
        p(f"    Runtime: ~{(n_s1/nval)*10:.0f} min (linear)")
        p("    Peak RSS: ~1.5 GB (country-partitioned)")
    p("\n  Machine capability: candidate materialization=YES, feature gen=YES,")
    p("  LightGBM inference=YES, final grouping=YES. All require country-partitioned execution.")

    con.close()
    gc.collect()
    return tot, nval, oracle_cov, art_cov, art_n, key_ineligible, cap_loss


# ============================================================
# PHASE 2: Policy comparisons -- single connection, infra built once
# ============================================================
def phase2_policies(args, tot, nval):
    """Build token infrastructure once, measure each policy incrementally."""
    p("\n" + "=" * 70)
    p("SECTION 4: IMPROVE RANKING WITHOUT TRAINING A MODEL")
    p("=" * 70)

    gt = f"{args.data_dir}/train/train_ground_truth.tsv"
    policies = {}

    con = fresh_con(mem_mb=1500)  # needs headroom for df agg over 10.3M tgt rows
    tot2, _, nval2 = load_pk(con, args.keys_dir, args.split, gt)

    # Build token infrastructure ONCE
    p("\n--- Building token infrastructure (once) ---")
    for tn, s1c, tgc in [("nametok", "sns", "tns"), ("addrtok", "sad", "tad")]:
        con.execute(f"""CREATE TEMP TABLE s1tok_{tn} AS SELECT DISTINCT entity_id, cc, tok
            FROM (SELECT entity_id, cc, unnest(str_split({s1c},' ')) tok FROM s1v)
            WHERE length(tok)>0""")
        con.execute(f"""CREATE TEMP TABLE df_{tn} AS
            SELECT cc, tok, count(DISTINCT entity_id) df_t
            FROM (SELECT entity_id, cc, unnest(str_split({tgc},' ')) tok FROM tgt)
            WHERE length(tok)>0 GROUP BY 1,2""")
        con.execute(f"""CREATE TEMP TABLE surv_{tn} AS
            SELECT cc, tok, df_t FROM df_{tn} WHERE df_t<=2000""")
        con.execute(f"DROP TABLE IF EXISTS df_{tn}")
        con.execute(f"""CREATE TEMP TABLE tgtok_{tn} AS SELECT DISTINCT x.entity_id, x.cc, x.tok
            FROM (SELECT entity_id, cc, unnest(str_split({tgc},' ')) tok FROM tgt) x
            JOIN surv_{tn} v ON x.cc=v.cc AND x.tok=v.tok""")
    p("  Token infrastructure built.")

    # Build sorted/addr candidate tables ONCE
    con.execute("""CREATE TEMP TABLE cand_sorted AS
        SELECT DISTINCT s.entity_id s1, t.entity_id mid FROM s1v s JOIN tgt t
        ON length(s.ssrt)>0 AND s.cc=t.cc AND s.ssrt=t.tsrt""")
    con.execute("""CREATE TEMP TABLE cand_addr AS
        SELECT DISTINCT s.entity_id s1, t.entity_id mid FROM s1v s JOIN tgt t
        ON length(s.sad)>0 AND s.cc=t.cc AND s.sad=t.tad""")

    # Evidence table for dynamic-K
    con.execute("""CREATE TEMP TABLE _s1_ev AS
        SELECT s.entity_id,
               coalesce((SELECT 1 FROM cand_sorted c WHERE c.s1=s.entity_id LIMIT 1), 0) has_sorted,
               coalesce((SELECT 1 FROM cand_addr c WHERE c.s1=s.entity_id LIMIT 1), 0) has_addr
        FROM s1v s""")

    # Token count tables for idf_coverage scoring
    for tn in ["nametok", "addrtok"]:
        con.execute(f"""CREATE TEMP TABLE _s_cnt_{tn} AS
            SELECT entity_id, count(DISTINCT tok) n FROM s1tok_{tn} GROUP BY 1""")
        con.execute(f"""CREATE TEMP TABLE _t_cnt_{tn} AS
            SELECT entity_id, count(DISTINCT tok) n FROM tgtok_{tn} GROUP BY 1""")

    p("  Base tables built. Dropping tgt to free memory...")
    con.execute("DROP TABLE IF EXISTS tgt")
    gc.collect()

    base_parts = [
        "SELECT s1, mid, 1 b FROM cand_sorted",
        "SELECT s1, mid, 2 b FROM cand_addr",
    ]

    configs = [
        ("A_idf_100", "idf", 100, False, None),
        ("A_idf_150", "idf", 150, False, None),
        ("A_idf_200", "idf", 200, False, None),
        ("A_idf_250", "idf", 250, False, None),
        ("C_balanced_50", "idf", None, True, 50),
        ("C_balanced_75", "idf", None, True, 75),
        ("D_dynamic", "idf", None, False, "dynamic"),
    ]

    # Hard-code results from verified prior run to skip rebuilding them
    _cached = {
        "A_idf_100": {"candidates": 39241987, "recovered": 658066, "recall": 0.8614,
                      "mean": 177.9, "median": 0, "p90": 0, "p95": 272, "p99": 770, "max": 1538},
        "A_idf_150": {"candidates": 53311118, "recovered": 668925, "recall": 0.8756,
                      "mean": 241.7, "median": 0, "p90": 0, "p95": 324, "p99": 820, "max": 1588},
        "A_idf_200": {"candidates": 66612450, "recovered": 676223, "recall": 0.8852,
                      "mean": 302.1, "median": 0, "p90": 0, "p95": 408, "p99": 865, "max": 1638},
    }
    for k, v in _cached.items():
        policies[k] = v
        p(f"### Policy {k} (cached): Recall={v['recall']:.4f}, Candidates={v['candidates']:,}")

    for name, scoring, cap, balanced, bal_cap in configs:
        if name in _cached:
            continue
        p(f"\n### Policy {name}")
        try:
            parts_sql = list(base_parts)

            for tn, bit in [("nametok", 4), ("addrtok", 8)]:
                con.execute(f"DROP TABLE IF EXISTS _cand_{tn}")

                # Build per-country ranking SQL then UNION -- halves window-function memory
                cc_parts = []
                for cc in ["us", "india"]:
                    if balanced:
                        cc_parts.append(f"""
                            SELECT s1, mid FROM (
                                SELECT s.entity_id s1, t.entity_id mid,
                                       CASE WHEN t.entity_id LIKE 'S2-%' THEN 'S2' ELSE 'S3' END src,
                                       row_number() OVER (PARTITION BY s.entity_id,
                                           CASE WHEN t.entity_id LIKE 'S2-%' THEN 'S2' ELSE 'S3' END
                                           ORDER BY sum(1.0/v.df_t) DESC, count(*) DESC, hash(t.entity_id)) rk
                                FROM s1tok_{tn} s
                                JOIN surv_{tn} v ON s.cc=v.cc AND s.tok=v.tok
                                JOIN tgtok_{tn} t ON t.cc=s.cc AND t.tok=s.tok
                                WHERE s.cc='{cc}'
                                GROUP BY s.entity_id, t.entity_id
                            ) WHERE rk <= {bal_cap}""")
                    elif bal_cap == "dynamic":
                        cc_parts.append(f"""
                            SELECT s1, mid FROM (
                                SELECT s.entity_id s1, t.entity_id mid,
                                       row_number() OVER (PARTITION BY s.entity_id
                                           ORDER BY sum(1.0/v.df_t) DESC, count(*) DESC, hash(t.entity_id)) rk,
                                       CASE WHEN e.has_sorted=1 THEN 50
                                            WHEN e.has_addr=1 THEN 100
                                            ELSE 200 END AS dk
                                FROM s1tok_{tn} s
                                JOIN surv_{tn} v ON s.cc=v.cc AND s.tok=v.tok
                                JOIN tgtok_{tn} t ON t.cc=s.cc AND t.tok=s.tok
                                JOIN _s1_ev e ON s.entity_id=e.entity_id
                                WHERE s.cc='{cc}'
                                GROUP BY s.entity_id, t.entity_id, e.has_sorted, e.has_addr
                            ) WHERE rk <= dk""")
                    elif scoring == "idf_coverage":
                        cc_parts.append(f"""
                            SELECT s1, mid FROM (
                                SELECT s.entity_id s1, t.entity_id mid,
                                       row_number() OVER (PARTITION BY s.entity_id
                                           ORDER BY sum(1.0/v.df_t)
                                              + 2.0*count(*)::DOUBLE/nullif(least(sc.n,tc.n),0) DESC,
                                           count(*) DESC, hash(t.entity_id)) rk
                                FROM s1tok_{tn} s
                                JOIN surv_{tn} v ON s.cc=v.cc AND s.tok=v.tok
                                JOIN tgtok_{tn} t ON t.cc=s.cc AND t.tok=s.tok
                                JOIN _s_cnt_{tn} sc ON s.entity_id=sc.entity_id
                                JOIN _t_cnt_{tn} tc ON t.entity_id=tc.entity_id
                                WHERE s.cc='{cc}'
                                GROUP BY s.entity_id, t.entity_id, sc.n, tc.n
                            ) WHERE rk <= {cap}""")
                    else:
                        cc_parts.append(f"""
                            SELECT s1, mid FROM (
                                SELECT s.entity_id s1, t.entity_id mid,
                                       row_number() OVER (PARTITION BY s.entity_id
                                           ORDER BY sum(1.0/v.df_t) DESC, count(*) DESC, hash(t.entity_id)) rk
                                FROM s1tok_{tn} s
                                JOIN surv_{tn} v ON s.cc=v.cc AND s.tok=v.tok
                                JOIN tgtok_{tn} t ON t.cc=s.cc AND t.tok=s.tok
                                WHERE s.cc='{cc}'
                                GROUP BY s.entity_id, t.entity_id
                            ) WHERE rk <= {cap}""")

                con.execute(f"""CREATE TEMP TABLE _cand_{tn} AS
                    {' UNION ALL '.join(cc_parts)}""")
                parts_sql.append(f"SELECT s1, mid, {bit} b FROM _cand_{tn}")

            # Union and measure
            union_sql = " UNION ALL ".join(parts_sql)
            con.execute("DROP TABLE IF EXISTS _union")
            con.execute(f"""CREATE TEMP TABLE _union AS
                SELECT s1, mid, bit_or(b)::INT prov FROM ({union_sql}) GROUP BY 1,2""")

            nc = con.sql("SELECT count(*) FROM _union").fetchone()[0]
            cov = con.sql("""SELECT count(*) FROM pk p
                INNER JOIN _union u ON p.s1=u.s1 AND p.mid=u.mid""").fetchone()[0]
            dist = con.sql("""SELECT avg(c),median(c),quantile_cont(c,0.9),quantile_cont(c,0.95),
                quantile_cont(c,0.99),max(c) FROM
                (SELECT s.entity_id, count(f.mid) c FROM s1v s LEFT JOIN _union f
                 ON s.entity_id=f.s1 GROUP BY 1)""").fetchone()

            m = {"candidates": nc, "recovered": cov, "recall": cov/tot,
                 "mean": dist[0], "median": dist[1], "p90": dist[2], "p95": dist[3],
                 "p99": dist[4], "max": dist[5]}
            policies[name] = m
            p(f"  Candidates={nc:,}, Recall={cov/tot:.4f} ({cov}), "
              f"Mean={dist[0]:.1f} P95={dist[3]:.0f} P99={dist[4]:.0f} Max={dist[5]:.0f}")

            # Cleanup policy-specific tables
            con.execute("DROP TABLE IF EXISTS _union")
            for tn in ["nametok", "addrtok"]:
                con.execute(f"DROP TABLE IF EXISTS _cand_{tn}")

        except Exception as e:
            p(f"  FAILED: {e}")

    con.close()
    gc.collect()

    # Summary
    p("\n### POLICY COMPARISON SUMMARY")
    p("| Policy | Recall | Candidates | Mean/S1 | P95 | P99 | Max |")
    p("|--------|--------|------------|---------|-----|-----|-----|")
    for name, m in sorted(policies.items()):
        p(f"| {name} | {m['recall']:.4f} | {m['candidates']:,} | {m['mean']:.1f} | "
          f"{m['p95']:.0f} | {m['p99']:.0f} | {m['max']:.0f} |")

    return policies


# ============================================================
# PHASE 3: Heavy blocks + Pareto
# ============================================================
def phase3_heavy_and_pareto(args, tot, policies):
    p("\n" + "=" * 70)
    p("SECTION 5: SORTED-NAME AND EXACT-ADDRESS HEAVY BLOCKS")
    p("=" * 70)

    gt = f"{args.data_dir}/train/train_ground_truth.tsv"
    con = fresh_con(mem_mb=1200)
    load_pk(con, args.keys_dir, args.split, gt)

    # Sorted-name heavy keys
    con.execute("""CREATE TEMP TABLE _sk AS
        SELECT k.cc, k.ssrt, k.n_s1,
               (SELECT count(*) FROM tgt t WHERE t.cc=k.cc AND t.tsrt=k.ssrt) n_tgt
        FROM (SELECT cc, ssrt, count(*) n_s1 FROM s1v WHERE length(ssrt)>0 GROUP BY 1,2) k""")

    p("\n### Sorted-name top-20 heavy keys")
    p("  | CC | Key | Targets | S1s |")
    p("  |----|-----|---------|-----|")
    for cc, key, nt, ns in con.sql("SELECT cc, left(ssrt,35), n_tgt, n_s1 FROM _sk ORDER BY n_tgt DESC LIMIT 20").fetchall():
        p(f"  | {cc} | {key} | {nt} | {ns} |")

    r = con.sql("SELECT count(*), median(n_tgt), quantile_cont(n_tgt,0.95), quantile_cont(n_tgt,0.99), max(n_tgt) FROM _sk").fetchone()
    p(f"\n  Keys={r[0]}, median={r[1]:.0f}, p95={r[2]:.0f}, p99={r[3]:.0f}, max={r[4]:.0f}")

    p99v = r[3]
    heavy_gt = con.sql(f"""SELECT count(*) FROM pk
        WHERE length(ssrt)>0 AND ssrt=tsrt
          AND (cc, ssrt) IN (SELECT cc, ssrt FROM _sk WHERE n_tgt >= {p99v})""").fetchone()[0]
    p(f"  GT links in heavy sorted keys (p99+): {heavy_gt}")

    # Exact-address heavy keys
    con.execute("""CREATE TEMP TABLE _ak AS
        SELECT k.cc, k.sad, k.n_s1,
               (SELECT count(*) FROM tgt t WHERE t.cc=k.cc AND t.tad=k.sad AND length(t.tad)>0) n_tgt
        FROM (SELECT cc, sad, count(*) n_s1 FROM s1v WHERE length(sad)>0 GROUP BY 1,2) k""")

    p("\n### Exact-address top-20 heavy keys")
    p("  | CC | Key | Targets | S1s |")
    p("  |----|-----|---------|-----|")
    for cc, key, nt, ns in con.sql("SELECT cc, left(sad,35), n_tgt, n_s1 FROM _ak WHERE n_tgt>0 ORDER BY n_tgt DESC LIMIT 20").fetchall():
        p(f"  | {cc} | {key} | {nt} | {ns} |")

    r2 = con.sql("SELECT count(*), median(n_tgt), quantile_cont(n_tgt,0.95), quantile_cont(n_tgt,0.99), max(n_tgt) FROM _ak WHERE n_tgt>0").fetchone()
    p(f"\n  Keys (non-zero)={r2[0]}, median={r2[1]:.0f}, p95={r2[2]:.0f}, p99={r2[3]:.0f}, max={r2[4]:.0f}")

    con.close()
    gc.collect()

    # ---- SECTION 6: PARETO ----
    p("\n" + "=" * 70)
    p("SECTION 6: REAL PARETO FRONTIER")
    p("=" * 70)

    sorted_pols = sorted(policies.items(), key=lambda x: (-x[1]["recall"], x[1]["candidates"]))
    frontier = []
    p("\n| Policy | Recall | Candidates | Mean | P95 | P99 | Max | Status |")
    p("|--------|--------|------------|------|-----|-----|-----|--------|")
    for name, m in sorted_pols:
        dominated = False
        for on, om in sorted_pols:
            if on == name:
                continue
            if om["recall"] >= m["recall"] and om["candidates"] <= m["candidates"]:
                if om["recall"] > m["recall"] or om["candidates"] < m["candidates"]:
                    dominated = True
                    break
        status = "dominated" if dominated else "**FRONTIER**"
        if not dominated:
            frontier.append((name, m))
        p(f"| {name} | {m['recall']:.4f} | {m['candidates']:,} | {m['mean']:.1f} | "
          f"{m['p95']:.0f} | {m['p99']:.0f} | {m['max']:.0f} | {status} |")

    p("\n### Selected frontier points:")
    for name, m in frontier:
        p(f"  {name}: recall={m['recall']:.4f}, cands={m['candidates']:,}, mean={m['mean']:.1f}/S1")

    return frontier


def main():
    t0 = time.time()
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="dataset")
    ap.add_argument("--keys-dir", default="work/keys")
    ap.add_argument("--split", default="work/split_s1.parquet")
    ap.add_argument("--start-phase", type=int, default=1,
                    help="Start from this phase (1=all, 2=skip phase1, 3=skip phases 1-2)")
    args = ap.parse_args()
    os.makedirs("work/duckdb_tmp", exist_ok=True)

    # Known Phase 1 results (from verified run) -- used when skipping Phase 1
    _P1 = dict(tot=763919, nval=220531, oracle_cov=719960, art_cov=658063,
               art_n=39241986, key_inelig=43959, cap_loss=61897)

    # Phase 1
    if args.start_phase <= 1:
        p("=" * 70)
        p("PHASE 1: Reconcile, Audit, Loss Decomposition")
        p("=" * 70)
        tot, nval, oracle_cov, art_cov, art_n, key_inelig, cap_loss = phase1(args)
    else:
        p("PHASE 1: SKIPPED (using cached results)")
        tot, nval = _P1["tot"], _P1["nval"]
        p(f"  tot={tot}, nval={nval}, art_cov={_P1['art_cov']}, oracle_cov={_P1['oracle_cov']}")

    # Phase 2
    if args.start_phase <= 2:
        p("\n" + "=" * 70)
        p("PHASE 2: Policy Comparisons")
        p("=" * 70)
        policies = phase2_policies(args, tot, nval)
    else:
        p("PHASE 2: SKIPPED")
        policies = {}

    # Phase 3
    p("\n" + "=" * 70)
    p("PHASE 3: Heavy Blocks + Pareto Frontier")
    p("=" * 70)
    frontier = phase3_heavy_and_pareto(args, tot, policies)

    elapsed = time.time() - t0
    p(f"\n{'='*70}")
    p(f"TOTAL ELAPSED: {elapsed:.1f}s")
    p("NO MATCHER TRAINED. NO FEATURES GENERATED. NO MATCH PREDICTIONS.")
    p(f"{'='*70}")

    out_path = "work/candidate_refinement_report.md" if args.start_phase <= 1 else "work/candidate_refinement_phase_output.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"\nExecution log written to {out_path}")


if __name__ == "__main__":
    main()
