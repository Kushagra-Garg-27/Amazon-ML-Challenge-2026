"""Foundation review-gate audit (items 2, 3, 4) — evidence, not new blockers.

Bounded-memory (DuckDB memory_limit=512MB, count/aggregate over the small 763,919-row
val-GT-pair table; never materializes the candidate cross-product). Reads only:
  work/keys/train_s{1,2,3}.parquet, work/split_s1.parquet, dataset/train/train_ground_truth.tsv
Writes: work/foundation_audit.md and (item 2) work/split_s1_grouped.parquet.
TRAINING DATA ONLY — no test rows are read (leakage analysis stays label-safe).

Run under the honest process-peak probe:
  PYTHONUTF8=1 .venv/Scripts/python scripts/measure_peak.py \
      --script scripts/foundation_audit.py -- --data-dir dataset
"""
import argparse
import os
import sys

import duckdb

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

L = []
def p(s=""):
    L.append(s)
    print(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="dataset")
    ap.add_argument("--keys-dir", default="work/keys")
    ap.add_argument("--split", default="work/split_s1.parquet")
    ap.add_argument("--val-frac", type=float, default=0.10)
    args = ap.parse_args()
    gt = f"{args.data_dir}/train/train_ground_truth.tsv"
    k = args.keys_dir
    os.makedirs("work/duckdb_tmp", exist_ok=True)

    con = duckdb.connect()
    con.execute("SET memory_limit='512MB'; SET threads=4;")
    con.execute("SET temp_directory='work/duckdb_tmp'; SET preserve_insertion_order=false;")
    con.execute("""CREATE MACRO jac(a,b) AS (
        len(list_intersect(str_split(a,' '), str_split(b,' ')))::DOUBLE
        / nullif(len(list_distinct(list_concat(str_split(a,' '), str_split(b,' ')))),0))""")
    con.execute("""CREATE MACRO scr(x) AS (
        CASE WHEN regexp_matches(x,'[\\x{0900}-\\x{097F}]') AND regexp_matches(x,'[a-z]') THEN 'mixed'
             WHEN regexp_matches(x,'[\\x{0900}-\\x{097F}]') THEN 'deva'
             WHEN regexp_matches(x,'[a-z]') THEN 'latin'
             ELSE 'other' END)""")

    # ---- shared base tables (val S1 keys, targets, val GT pairs) ----
    con.execute(f"""CREATE TEMP TABLE s1v AS
        SELECT a.entity_id, a.country_norm cc, a.name_norm sn, a.name_nosuffix sns,
               a.name_sorted ssrt, a.addr_norm sad
        FROM read_parquet('{k}/train_s1.parquet') a
        JOIN read_parquet('{args.split}') s
          ON a.entity_id=s.entity_id AND s.split='val'""")
    con.execute(f"""CREATE TEMP TABLE tgt AS
        SELECT entity_id, country_norm cc, name_norm tn, name_nosuffix tns,
               name_sorted tsrt, addr_norm tad FROM read_parquet('{k}/train_s2.parquet')
        UNION ALL
        SELECT entity_id, country_norm, name_norm, name_nosuffix, name_sorted, addr_norm
          FROM read_parquet('{k}/train_s3.parquet')""")
    con.execute(f"""CREATE TEMP TABLE gtp AS
        SELECT g.source1_entity_id s1, trim(x) mid
        FROM read_csv('{gt}', delim='\t', header=true, quote='', all_varchar=true) g,
             UNNEST(string_split(g.matched_entity_ids, ',')) AS u(x)
        WHERE g.matched_entity_ids IS NOT NULL AND length(trim(g.matched_entity_ids))>0
          AND g.source1_entity_id IN (SELECT entity_id FROM s1v)""")
    # per-pair table with BOTH sides' keys (763,919 rows — bounded)
    con.execute("""CREATE TEMP TABLE pk AS
        SELECT g.s1, g.mid, s.cc, s.sn, s.sns, s.ssrt, s.sad,
               t.tn, t.tns, t.tsrt, t.tad,
               (length(s.sns)>0 AND s.cc=t.cc AND s.sns=t.tns) AS covered
        FROM gtp g JOIN s1v s ON g.s1=s.entity_id JOIN tgt t ON g.mid=t.entity_id""")
    tot, cov = con.sql("SELECT count(*), count(*) FILTER(WHERE covered) FROM pk").fetchone()
    lost = tot - cov
    p("# Foundation audit — items 2 (leakage), 3 (lost-pair anatomy), 4 (baseline math)\n")
    p(f"Base: val GT pairs={tot}, exact-key covered={cov} (recall={cov/tot:.4f}), "
      f"lost={lost}. [reproduces work/baseline_blocking.md]\n")

    audit_item4(con, args)
    audit_item3(con, tot, lost)
    audit_item2(con, args)

    with open("work/foundation_audit.md", "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def audit_item4(con, args):
    """Verify baseline candidate math against a materialized distinct join; clarify
    exact vs sorted (standalone vs union) with exact incremental GT links."""
    p("## Item 4 — baseline math verified against a materialized distinct join\n")
    k = args.keys_dir
    # per-key target counts for the exact (country, name_nosuffix) key
    con.execute("""CREATE TEMP TABLE tkc AS
        SELECT cc, tns, count(*) tc FROM tgt WHERE length(tns)>0 GROUP BY 1,2""")
    # (a) reproduce total exact candidate count via count-per-key over ALL val S1
    total_predicted = con.sql("""SELECT coalesce(sum(CASE WHEN length(s.sns)=0 THEN 0
              ELSE coalesce(t.tc,0) END),0)
        FROM s1v s LEFT JOIN tkc t ON s.cc=t.cc AND s.sns=t.tns""").fetchone()[0]
    p(f"- exact candidate pairs via count-per-key (all val S1) = {total_predicted} "
      f"[baseline_blocking.md reported 8,555,794]")
    # (b) materialize the ACTUAL distinct join for a deterministic 1/50 sample of val S1
    con.execute("""CREATE TEMP TABLE samp AS
        SELECT * FROM s1v WHERE abs(hash(entity_id)) % 50 = 0""")
    n_samp = con.sql("SELECT count(*) FROM samp").fetchone()[0]
    pred_s = con.sql("""SELECT coalesce(sum(CASE WHEN length(s.sns)=0 THEN 0
              ELSE coalesce(t.tc,0) END),0)
        FROM samp s LEFT JOIN tkc t ON s.cc=t.cc AND s.sns=t.tns""").fetchone()[0]
    # actual join: every distinct (s1, target) actually produced by the exact rule
    con.execute("""CREATE TEMP TABLE realjoin AS
        SELECT s.entity_id s1, t.entity_id mid
        FROM samp s JOIN tgt t ON length(s.sns)>0 AND s.cc=t.cc AND s.sns=t.tns""")
    act, actd = con.sql(
        "SELECT count(*), count(DISTINCT (s1,mid)) FROM realjoin").fetchone()
    p(f"- sample={n_samp} val S1 (deterministic hash%50=0): predicted-by-key={pred_s}, "
      f"materialized join rows={act}, distinct (s1,target)={actd}")
    p(f"  → prediction {'EXACTLY matches' if pred_s==act==actd else 'DIFFERS from'} "
      f"the distinct join (targets are id-unique & S2/S3 disjoint ⇒ no double-count).\n")
    # (c) exact ⊆ sorted?  name_sorted = sorted(set(tokens of name_nosuffix)) ⇒ analytically
    #     equal name_nosuffix ⇒ equal name_sorted. Confirm empirically on the sample: any
    #     candidate matching the exact key but NOT the sorted key.
    viol = con.sql("""SELECT count(*) FROM samp s JOIN tgt t
        ON length(s.sns)>0 AND s.cc=t.cc AND s.sns=t.tns
        WHERE NOT (s.cc=t.cc AND s.ssrt=t.tsrt)""").fetchone()[0]
    p(f"- exact⊆sorted check: candidates matching exact-key but NOT sorted-key = {viol} "
      f"(0 expected: name_sorted is sorted(set(tokens(name_nosuffix)))).")
    # sorted standalone recall + candidate count, and incremental over exact
    cov_sorted = con.sql("""SELECT count(*) FROM pk
        WHERE length(ssrt)>0 AND cc IS NOT NULL AND ssrt=tsrt""").fetchone()[0]
    cov_exact = con.sql("SELECT count(*) FILTER(WHERE covered) FROM pk").fetchone()[0]
    tot_pairs = con.sql("SELECT count(*) FROM pk").fetchone()[0]
    con.execute("""CREATE TEMP TABLE skc AS
        SELECT cc, tsrt, count(*) tc FROM tgt WHERE length(tsrt)>0 GROUP BY 1,2""")
    sorted_cands = con.sql("""SELECT coalesce(sum(CASE WHEN length(s.ssrt)=0 THEN 0
              ELSE coalesce(t.tc,0) END),0)
        FROM s1v s LEFT JOIN skc t ON s.cc=t.cc AND s.ssrt=t.tsrt""").fetchone()[0]
    p(f"- sorted key STANDALONE: recall={cov_sorted/tot_pairs:.4f} ({cov_sorted}/{tot_pairs}), "
      f"candidate pairs={sorted_cands} [baseline diag: 0.5061 / 9,060,998]")
    p(f"- UNION(exact,sorted): since exact⊆sorted, union == sorted. Incremental GT links "
      f"recovered beyond exact = {cov_sorted-cov_exact}; incremental candidate pairs = "
      f"{sorted_cands-total_predicted}.\n")


def audit_item3(con, tot, lost):
    """Anatomy of the GT pairs LOST by exact-name blocking — every count with a
    denominator so each candidate technique's recoverable population is sized."""
    p(f"## Item 3 — anatomy of the {lost} lost GT pairs (denominator shown per cut)\n")
    con.execute("CREATE TEMP TABLE lp AS SELECT * FROM pk WHERE NOT covered")

    def rows(sql):
        return con.sql(sql).fetchall()

    # (1) by country: lost vs that country's total val GT pairs
    p("### by country (lost / country total pairs)")
    for c, ct, ls in rows("""SELECT p.cc, count(*) ct,
            count(*) FILTER(WHERE NOT p.covered) ls FROM pk p GROUP BY 1 ORDER BY 2 DESC"""):
        p(f"  - {c}: lost={ls}/{ct} ({100*ls/ct:.1f}% of that country's pairs)")
    # (2) script class each side + (3) cross-script
    p("\n### name script per side (over lost)")
    for side, col in (("S1", "sn"), ("target", "tn")):
        dist = rows(f"SELECT scr({col}) s, count(*) n FROM lp GROUP BY 1 ORDER BY 2 DESC")
        p(f"  - {side}: " + ", ".join(f"{s}={n} ({100*n/lost:.1f}%)" for s, n in dist))
    xs = con.sql("""SELECT count(*) FROM lp
        WHERE (scr(sn)='deva' AND scr(tn)='latin') OR (scr(sn)='latin' AND scr(tn)='deva')
        """).fetchone()[0]
    p(f"  - cross-script (one side deva-only, other latin-only) = {xs} "
      f"({100*xs/lost:.1f}% of lost) — DESCRIPTIVE fraction of lost pairs that are "
      f"cross-script; NOT a formal transliteration ceiling (says nothing about what a "
      f"transliteration blocker would actually retrieve)")
    # (4) token Jaccard of name_nosuffix, (5) sorted-name equality
    p("\n### name_nosuffix token Jaccard (over lost)")
    for lo, hi, lbl in ((-0.01, 0.0, "=0"), (0.0, 0.5, "(0,0.5)"),
                        (0.5, 0.999, "[0.5,1)"), (0.999, 1.01, "=1")):
        n = con.sql(f"SELECT count(*) FROM lp WHERE jac(sns,tns) > {lo} "
                    f"AND jac(sns,tns) <= {hi}").fetchone()[0]
        p(f"  - Jaccard {lbl}: {n} ({100*n/lost:.1f}%)")
    seq = con.sql("SELECT count(*) FROM lp WHERE cc IS NOT NULL AND ssrt=tsrt").fetchone()[0]
    p(f"  - sorted-name EQUAL (word-order-only difference) = {seq} ({100*seq/lost:.1f}%) "
      f"— retrieved by the sorted-name blocker (an actual retrieval pass, measured below)")
    # (6) address presence combos + similarity where both present
    p("\n### address (addr_norm) over lost")
    for lbl, cond in (("both present", "length(sad)>0 AND length(tad)>0"),
                      ("only S1", "length(sad)>0 AND length(tad)=0"),
                      ("only target", "length(sad)=0 AND length(tad)>0"),
                      ("neither", "length(sad)=0 AND length(tad)=0")):
        n = con.sql(f"SELECT count(*) FROM lp WHERE {cond}").fetchone()[0]
        p(f"  - {lbl}: {n} ({100*n/lost:.1f}%)")
    p("  address token Jaccard where both present (SIMILARITY OPPORTUNITY only — this is "
      "NOT retrieval; whether an address blocker actually returns these pairs is measured "
      "separately in the candidate-generation experiments):")
    both = con.sql("SELECT count(*) FROM lp WHERE length(sad)>0 AND length(tad)>0").fetchone()[0]
    for lo, hi, lbl in ((-0.01, 0.0, "=0"), (0.0, 0.5, "(0,0.5)"),
                        (0.5, 0.999, "[0.5,1)"), (0.999, 1.01, "=1")):
        n = con.sql(f"SELECT count(*) FROM lp WHERE length(sad)>0 AND length(tad)>0 "
                    f"AND jac(sad,tad) > {lo} AND jac(sad,tad) <= {hi}").fetchone()[0]
        p(f"    - Jaccard {lbl}: {n} ({100*n/both:.1f}% of both-present)")
    # (7) common-key frequency: does the lost pair's S1 get ANY exact candidates?
    p("\n### exact-key target frequency for the lost pair's S1 (does S1 get candidates?)")
    con.execute("""CREATE TEMP TABLE lpf AS
        SELECT l.*, coalesce(t.tc,0) kc FROM lp l
        LEFT JOIN tkc t ON l.cc=t.cc AND l.sns=t.tns""")
    for lo, hi, lbl in ((-1, 0, "0 (S1 has NO exact-key targets)"), (0, 10, "1-10"),
                        (10, 100, "11-100"), (100, 10**9, ">100")):
        n = con.sql(f"SELECT count(*) FROM lpf WHERE kc > {lo} AND kc <= {hi}").fetchone()[0]
        p(f"  - key freq {lbl}: {n} ({100*n/lost:.1f}%)")
    p("")


def audit_item2(con, args):
    """Correct the leakage claim: 'each target ≤1 S1' only proves no SHARED LABELED
    TARGET across the split — it says nothing about entity/near-duplicate leakage among
    S1 rows. Type the 46.3% cross-split name-key overlap by address similarity, then
    materialize a deterministic group-aware (name-key) holdout. TRAINING DATA ONLY."""
    p("## Item 2 — leakage assessment corrected + harder holdout materialized\n")
    k, sp = args.keys_dir, args.split
    con.execute(f"""CREATE TEMP TABLE s1all AS
        SELECT a.entity_id, a.country_norm cc, a.name_nosuffix sns, a.addr_norm ad, s.split
        FROM read_parquet('{k}/train_s1.parquet') a
        JOIN read_parquet('{sp}') s ON a.entity_id=s.entity_id""")
    ntr, nvl = con.sql("""SELECT count(*) FILTER(WHERE split='train'),
        count(*) FILTER(WHERE split='val') FROM s1all""").fetchone()
    p(f"- original split (PRESERVED at {sp}): train S1={ntr}, val S1={nvl}")
    p("- WHY the old claim is overstated: 'each S2/S3 matches ≤1 S1' establishes only the "
      "observed LABELED-TARGET uniqueness property (no S2/S3 id is shared across S1 rows). "
      "It is NOT proof of no entity leakage: two DISTINCT S1 rows (one train, one val) can "
      "still be the same or a near-duplicate business, which target-uniqueness cannot exclude.\n")
    # collision keys reproduce (via group sizes, no INTERSECT/cross-product)
    con.execute("""CREATE TEMP TABLE keysz AS
        SELECT cc, sns, count(*) FILTER(WHERE split='train') trn,
                        count(*) FILTER(WHERE split='val') vln
        FROM s1all WHERE length(sns)>0 GROUP BY 1,2 HAVING trn>0 AND vln>0""")
    nck, val_aff = con.sql("SELECT count(*), sum(vln) FROM keysz").fetchone()
    p(f"- cross-split (country,name_nosuffix) collision keys={nck}; val S1 sharing a "
      f"train name-key={val_aff} ({100*val_aff/nvl:.3f}% of val) [manifest: 55,627 / 46.312%]")
    ngen = con.sql("SELECT count(*) FROM keysz WHERE trn>5 OR vln>5").fetchone()[0]
    # TYPE the collisions on RARE keys (≤5 each side ⇒ ≤25 cross pairs): for each key take
    # the MAX address Jaccard over ALL cross-split S1 pairs — high⇒a near-duplicate entity
    # bridges the split (true leakage); this is far stronger than a single representative.
    con.execute("""CREATE TEMP TABLE small AS SELECT cc,sns FROM keysz WHERE trn<=5 AND vln<=5""")
    con.execute("""CREATE TEMP TABLE keymax AS
        SELECT s.cc, s.sns,
               max(CASE WHEN length(t.ad)>0 AND length(v.ad)>0 THEN jac(t.ad,v.ad) END) mj
        FROM small s
        JOIN s1all t ON t.split='train' AND t.cc=s.cc AND t.sns=s.sns
        JOIN s1all v ON v.split='val'   AND v.cc=s.cc AND v.sns=s.sns
        GROUP BY 1,2""")
    nsmall = con.sql("SELECT count(*) FROM keymax").fetchone()[0]
    nwith = con.sql("SELECT count(*) FROM keymax WHERE mj IS NOT NULL").fetchone()[0]
    p(f"- collision TYPING on {nsmall} RARE keys (≤5 S1 each side; the other {ngen} keys are "
      f"generic/high-frequency names). Per key = MAX address token Jaccard over ALL "
      f"cross-split S1 pairs ({nwith} keys have ≥1 both-address pair):")
    for lo, hi, lbl in ((-0.01, 0.0, "=0 (distinct businesses, same name)"),
                        (0.0, 0.5, "(0,0.5)"), (0.5, 0.8, "[0.5,0.8)"),
                        (0.8, 0.999, "[0.8,1) near-dup"), (0.999, 1.01, "=1 identical addr")):
        n = con.sql(f"SELECT count(*) FROM keymax WHERE mj > {lo} AND mj <= {hi}").fetchone()[0]
        p(f"    - max addr Jac {lbl}: {n} ({100*n/nwith:.1f}% of typed)")
    nd = con.sql("SELECT count(*) FROM keymax WHERE mj>=0.8").fetchone()[0]
    p(f"  → {nd} rare-name keys ({100*nd/nwith:.1f}% of typed) have a cross-split S1 pair with "
      f"address Jaccard≥0.8. SCOPE: this is evidence for the PROBED SUBSET only (rare keys, "
      f"≤5 S1/side, both addresses present). It does NOT generalize to the generic-name keys "
      f"or to fuzzy/typo/translit name variants, and does NOT prove the split is leakage-free — "
      f"it bounds address-corroborated near-duplication in this one measurable slice.\n")
    # HARDER HOLDOUT: group-aware by (country, name_nosuffix) — all S1 with a key on ONE side
    con.execute(f"""CREATE TEMP TABLE grp AS
        SELECT entity_id, cc, sns,
          CASE WHEN (abs(hash('grpseed1:' || cc || '|' || sns)) % 1000000)
                    < {int(args.val_frac*1_000_000)} THEN 'val' ELSE 'train' END AS split
        FROM s1all""")
    gtr, gvl = con.sql("""SELECT count(*) FILTER(WHERE split='train'),
        count(*) FILTER(WHERE split='val') FROM grp""").fetchone()
    leak = con.sql("""SELECT count(*) FROM (
        SELECT cc,sns FROM grp WHERE split='train' INTERSECT
        SELECT cc,sns FROM grp WHERE split='val')""").fetchone()[0]
    out_grp = "work/split_s1_grouped.parquet"
    if os.path.exists(out_grp):
        p(f"- HARDER HOLDOUT already materialized (FROZEN, not regenerated) → {out_grp}")
    else:
        con.execute(f"COPY (SELECT entity_id, cc AS country_norm, sns AS name_nosuffix, split "
                    f"FROM grp) TO '{out_grp}' (FORMAT parquet)")
        p(f"- HARDER HOLDOUT materialized → {out_grp}")
    p(f"  train S1={gtr} ({100*gtr/(gtr+gvl):.2f}%), val S1={gvl} ({100*gvl/(gtr+gvl):.2f}%); "
      f"cross-split name-key overlap = {leak} (0 by construction)")
    p("  WHAT IT IS: a distribution-shift / stress-test diagnostic — S1 whose exact "
      "(country,name_nosuffix) key was never seen on the other side. It is NOT a guaranteed "
      "lower-bound score and NOT a leaderboard/competition estimate.")
    p("  LIMITS: (a) removes EXACT name-key overlap only, not fuzzy/near-dup overlap "
      "(typo/translit variants still bridge the split); (b) US/India only — France remains "
      "genuinely unseen; (c) grouping perturbs country mix slightly. Keep BOTH manifests: the "
      "random split for development/selection, this one as a distribution-shift diagnostic.\n")


if __name__ == "__main__":
    main()
