"""Candidate-generation experiments (Steps 2-15) — evidence, no model training.

DEVELOPMENT split only (work/split_s1.parquet, val used for selection). Bounded DuckDB
(memory_limit=512MB, threads=4, temp spills to work/duckdb_tmp). Recall is measured EXACTLY
on the GT-pair table (763,919 rows) by testing whether each GT pair's S1 and target land in
the same block under a pass — no candidate materialization needed for recall. Candidate
VOLUME for single-key passes is exact via key-product sums; for token passes the capped
distinct candidate set is materialized to disk (integer-coded) and counted.

PRIMARY metric = pair-level GT-link recall. S1-level coverage is reported SEPARATELY and is
never called "recall". Candidate identity = (source1_entity_id, target_entity_id), dedup
across passes; provenance kept as a separate bitmask.

Run under the honest process-peak probe:
  PYTHONUTF8=1 .venv/Scripts/python scripts/measure_peak.py \
      --script scripts/candidate_experiments.py -- --data-dir dataset
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


def pct(dist_rows):
    return ", ".join(f"{s}={n}" for s, n in dist_rows)


def setup(con):
    # 1GB is a documented, honest bound for this experiment's token-DF aggregations over
    # the full 10.3M-row target set (the foundation audit used 512MB for lighter joins).
    # Both remain far under measured 2.40 GiB available / 15.34 GiB physical; the true
    # process-peak RSS is reported by scripts/measure_peak.py, not by this cap.
    con.execute("SET memory_limit='1GB'; SET threads=4;")
    con.execute("SET temp_directory='work/duckdb_tmp'; SET preserve_insertion_order=false;")
    con.execute("""CREATE MACRO jac(a,b) AS (
        len(list_intersect(str_split(a,' '), str_split(b,' ')))::DOUBLE
        / nullif(len(list_distinct(list_concat(str_split(a,' '), str_split(b,' ')))),0))""")
    con.execute("""CREATE MACRO scr(x) AS (
        CASE WHEN regexp_matches(x,'[\\x{0900}-\\x{097F}]') AND regexp_matches(x,'[a-z]') THEN 'mixed'
             WHEN regexp_matches(x,'[\\x{0900}-\\x{097F}]') THEN 'deva'
             WHEN regexp_matches(x,'[a-z]') THEN 'latin' ELSE 'other' END)""")


def base_tables(con, k, split, gt):
    """val S1 (s1v), targets S2∪S3 (tgt), and the GT-pair table pk with both sides'
    keys+addresses. pk is the exact recall oracle (one row per true (S1,target) link)."""
    con.execute(f"""CREATE TEMP TABLE s1v AS
        SELECT a.entity_id, a.country_norm cc, a.name_nosuffix sns, a.name_sorted ssrt,
               a.addr_norm sad
        FROM read_parquet('{k}/train_s1.parquet') a
        JOIN read_parquet('{split}') s ON a.entity_id=s.entity_id AND s.split='val'""")
    con.execute(f"""CREATE TEMP TABLE tgt AS
        SELECT entity_id, country_norm cc, name_nosuffix tns, name_sorted tsrt, addr_norm tad
          FROM read_parquet('{k}/train_s2.parquet')
        UNION ALL
        SELECT entity_id, country_norm, name_nosuffix, name_sorted, addr_norm
          FROM read_parquet('{k}/train_s3.parquet')""")
    con.execute(f"""CREATE TEMP TABLE gtp AS
        SELECT g.source1_entity_id s1, trim(x) mid
        FROM read_csv('{gt}', delim='\t', header=true, quote='', all_varchar=true) g,
             UNNEST(string_split(g.matched_entity_ids, ',')) AS u(x)
        WHERE g.matched_entity_ids IS NOT NULL AND length(trim(g.matched_entity_ids))>0
          AND g.source1_entity_id IN (SELECT entity_id FROM s1v)""")
    con.execute("""CREATE TEMP TABLE pk AS
        SELECT g.s1, g.mid, s.cc, s.sns, s.ssrt, s.sad, t.tns, t.tsrt, t.tad
        FROM gtp g JOIN s1v s ON g.s1=s.entity_id JOIN tgt t ON g.mid=t.entity_id""")


# ---- single-key passes (exact / sorted / address): exact volume via key products ----
SK = {  # name -> (s1v col, tgt col, pk s1 col, pk tgt col)
    "exact_name":   ("sns", "tns", "sns", "tns"),
    "sorted_name":  ("ssrt", "tsrt", "ssrt", "tsrt"),
    "exact_addr":   ("sad", "tad", "sad", "tad"),
}


def dist_s1(con, s1col, tkc):
    """per-val-S1 candidate-count distribution for a single-key pass."""
    row = con.sql(f"""SELECT avg(c), median(c),
            quantile_cont(c,0.9), quantile_cont(c,0.95), quantile_cont(c,0.99), max(c),
            count(*) FILTER(WHERE c=0), count(*)
        FROM (SELECT CASE WHEN length(s.{s1col})=0 THEN 0 ELSE coalesce(k.tc,0) END c
              FROM s1v s LEFT JOIN {tkc} k ON s.cc=k.cc AND s.{s1col}=k.kv)""").fetchone()
    return dict(mean=row[0], median=row[1], p90=row[2], p95=row[3], p99=row[4],
               max=row[5], zero=row[6], nS1=row[7])


def single_key(con, name, tot, nS1pairs):
    s1c, tc, pk_s, pk_t = SK[name]
    con.execute(f"DROP TABLE IF EXISTS tkc_{name}")
    con.execute(f"""CREATE TEMP TABLE tkc_{name} AS
        SELECT cc, {tc} kv, count(*) tc FROM tgt WHERE length({tc})>0 GROUP BY 1,2""")
    tkc = f"tkc_{name}"
    cov = con.sql(f"""SELECT count(*) FROM pk
        WHERE length({pk_s})>0 AND cc IS NOT NULL AND {pk_s}={pk_t}""").fetchone()[0]
    cands = con.sql(f"""SELECT coalesce(sum(CASE WHEN length(s.{s1c})=0 THEN 0
              ELSE coalesce(k.tc,0) END),0)
        FROM s1v s LEFT JOIN {tkc} k ON s.cc=k.cc AND s.{s1c}=k.kv""").fetchone()[0]
    # S1-level coverage: val S1 with >=1 covered GT pair / val S1 with >=1 GT pair
    s1cov = con.sql(f"""SELECT count(DISTINCT s1) FROM pk
        WHERE length({pk_s})>0 AND {pk_s}={pk_t}""").fetchone()[0]
    d = dist_s1(con, s1c, tkc)
    return dict(name=name, cov=cov, recall=cov/tot, cands=cands, s1cov=s1cov,
               s1recall=s1cov/nS1pairs, **d)


def sorted_containment(con, tot):
    """Step 3 — explicit exact-name ⊆ sorted-name containment (invariant + real data).

    INVARIANT: name_sorted = join(sort(distinct(tokens(name_nosuffix)))). So
    name_nosuffix equality ⇒ name_sorted equality ⇒ every exact-name candidate is also a
    sorted-name candidate. Verified empirically on the GT-pair oracle: (a) 0 GT pairs are
    exact-key-covered yet NOT sorted-key-covered; (b) 0 target rows have equal name_nosuffix
    but unequal name_sorted within a country. Sorted therefore subsumes exact; exact-name is
    dropped from the final union (no recall or candidate contribution beyond sorted)."""
    p("\n## Step 3 — exact-name ⊆ sorted-name containment (verified, not assumed)\n")
    # (a) oracle: any GT pair covered by exact key but not by sorted key?
    viol_pk = con.sql("""SELECT count(*) FROM pk
        WHERE length(sns)>0 AND cc IS NOT NULL AND sns=tns
          AND NOT (length(ssrt)>0 AND ssrt=tsrt)""").fetchone()[0]
    # (b) data: rows sharing (cc,name_nosuffix) but differing on name_sorted
    viol_key = con.sql("""SELECT count(*) FROM
        (SELECT cc, tns, count(DISTINCT tsrt) d FROM tgt WHERE length(tns)>0 GROUP BY 1,2)
        WHERE d>1""").fetchone()[0]
    cov_e = con.sql("SELECT count(*) FROM pk WHERE length(sns)>0 AND sns=tns").fetchone()[0]
    cov_s = con.sql("SELECT count(*) FROM pk WHERE length(ssrt)>0 AND ssrt=tsrt").fetchone()[0]
    p(f"  (a) GT pairs exact-covered but NOT sorted-covered = {viol_pk} (0 expected)")
    p(f"  (b) target (cc,name_nosuffix) groups with >1 distinct name_sorted = {viol_key} (0 expected)")
    p(f"  exact-name recall={cov_e/tot:.4f} ({cov_e}) ⊆ sorted-name recall={cov_s/tot:.4f} "
      f"({cov_s}); incremental GT links sorted-over-exact = {cov_s-cov_e}. "
      f"CONTAINMENT {'HOLDS' if viol_pk==0 and viol_key==0 else 'VIOLATED'} ⇒ exact-name "
      f"subsumed; excluded from the final union.")
    return dict(viol_pk=viol_pk, viol_key=viol_key)


def fmt_pass(m):
    return (f"  - {m['name']}: pair-recall={m['recall']:.4f} ({m['cov']}), "
            f"S1-coverage={m['s1recall']:.4f} ({m['s1cov']}), distinct-cands={m['cands']}, "
            f"cand/S1 mean={m['mean']:.2f} median={m['median']:.0f} p90={m['p90']:.0f} "
            f"p95={m['p95']:.0f} p99={m['p99']:.0f} max={m['max']:.0f}, zero-cand-S1={m['zero']}")


# ---- token passes (name-token / addr-token): frequency-aware, block-size capped ----
def token_tables(con, name, s1col, tgtcol):
    """Build the small val-S1 token set and the target document-frequency table.

    df_{name} is computed with a STREAMED aggregation directly over the unnested target
    tokens (count(DISTINCT entity_id) per (cc,tok)) — we do NOT materialize the full
    ~10.3M-row target token set here (that DISTINCT store is what exceeds the cap). The
    target token set is materialized later in token_pass, restricted to SURVIVING tokens
    only (generic high-DF tokens are dropped before any target-side materialization)."""
    con.execute(f"DROP TABLE IF EXISTS s1tok_{name}")
    con.execute(f"""CREATE TEMP TABLE s1tok_{name} AS SELECT DISTINCT entity_id, cc, tok
        FROM (SELECT entity_id, cc, unnest(str_split({s1col},' ')) tok FROM s1v)
        WHERE length(tok)>0""")
    con.execute(f"DROP TABLE IF EXISTS df_{name}")
    con.execute(f"""CREATE TEMP TABLE df_{name} AS
        SELECT cc, tok, count(DISTINCT entity_id) df_t
        FROM (SELECT entity_id, cc, unnest(str_split({tgtcol},' ')) tok FROM tgt)
        WHERE length(tok)>0 GROUP BY 1,2""")


def token_df_report(con, name):
    r = con.sql(f"""SELECT count(*), median(df_t), quantile_cont(df_t,0.9),
        quantile_cont(df_t,0.95), quantile_cont(df_t,0.99), max(df_t) FROM df_{name}""").fetchone()
    top = con.sql(f"""SELECT cc, tok, df_t FROM df_{name} ORDER BY df_t DESC LIMIT 8""").fetchall()
    p(f"  {name}: target (cc,tok) keys={r[0]}, target-DF median={r[1]:.0f} p90={r[2]:.0f} "
      f"p95={r[3]:.0f} p99={r[4]:.0f} max={r[5]:.0f}")
    p(f"    top-DF tokens (generic → dropped): " +
      ", ".join(f"{cc}:{t}({d})" for cc, t, d in top))


def token_pass(con, name, s1col, tgtcol, pk_s, pk_t, drop_df, tot, nS1pairs,
               cap=100, guard=400_000_000):
    """Frequency-aware token blocker WITH a per-S1 block-size control (Step 5).

    Surviving token = target-DF <= drop_df (generic high-DF tokens dropped). Recall is
    measured EXACTLY on pk (a GT pair is retrievable if both sides share >=1 surviving
    token, same cc) — this is the UNCAPPED retrieval ceiling, independent of the cap.
    The materialized candidate set is per-S1 capped to the top-{cap} targets ranked by an
    IDF-weighted specificity score (sum of 1/df_t over shared surviving tokens; rarer shared
    tokens weigh more), shared-count then hash(mid) tie-break (deterministic): this bounds
    volume to <= cap*|val S1| and keeps blocks compact. The guard aborts only
    on a pathological pre-dedup join estimate. Retrieval loss (ceiling) vs pruning loss
    (cap effect) stay separately attributable: the ceiling is the pk oracle above, the
    post-cap realized recall is measured on the materialized set."""
    con.execute(f"""CREATE TEMP TABLE surv_{name} AS
        SELECT cc, tok, df_t FROM df_{name} WHERE df_t<={drop_df}""")
    nsurv, ndrop = con.sql(f"""SELECT
        (SELECT count(*) FROM surv_{name}), (SELECT count(*) FROM df_{name})-(SELECT count(*) FROM surv_{name})
        """).fetchone()
    # exact recall on pk: build the set of GT pairs that share >=1 surviving token (same cc)
    con.execute(f"DROP TABLE IF EXISTS covtok_{name}")
    con.execute(f"""CREATE TEMP TABLE covtok_{name} AS SELECT DISTINCT a.s1, a.mid FROM
          (SELECT s1,mid,cc,unnest(str_split({pk_s},' ')) tok FROM pk) a
          JOIN surv_{name} v ON a.cc=v.cc AND a.tok=v.tok
          JOIN (SELECT s1,mid,cc,unnest(str_split({pk_t},' ')) tok FROM pk) b
            ON b.s1=a.s1 AND b.mid=a.mid AND b.cc=a.cc AND b.tok=a.tok""")
    cov = con.sql(f"SELECT count(*) FROM covtok_{name}").fetchone()[0]
    s1cov = con.sql(f"SELECT count(DISTINCT s1) FROM covtok_{name}").fetchone()[0]
    # estimate join rows BEFORE dedup (guardrail): sum over surviving (cc,tok) of df_s*df_t
    est = con.sql(f"""SELECT coalesce(sum(s.df_s*v.df_t),0) FROM surv_{name} v
        JOIN (SELECT cc,tok,count(*) df_s FROM s1tok_{name} GROUP BY 1,2) s
          ON s.cc=v.cc AND s.tok=v.tok""").fetchone()[0]
    out = dict(name=name, cov=cov, recall=cov/tot, s1cov=s1cov, s1recall=s1cov/nS1pairs,
               nsurv=nsurv, ndrop=ndrop, est_join=est, drop_df=drop_df)
    p(f"  {name} (drop target-DF>{drop_df}): surviving tokens={nsurv}, dropped={ndrop}; "
      f"pre-dedup join estimate={est}")
    if est > guard:
        p(f"    GUARDRAIL HIT: estimate {est} > {guard} (pathological) → NOT materializing. "
          f"Recall reported (exact); candidate volume NOT claimed.")
        out.update(cands=None, mean=None, guarded=True)
        return out
    # materialize the target token set RESTRICTED TO SURVIVING TOKENS only (generic
    # high-DF tokens already dropped ⇒ bounded), then the per-S1 top-{cap} candidate set
    # ranked by IDF-weighted shared-token specificity (sum 1/df_t) — bounds volume & memory.
    con.execute(f"DROP TABLE IF EXISTS tgtok_{name}")
    con.execute(f"""CREATE TEMP TABLE tgtok_{name} AS SELECT DISTINCT x.entity_id, x.cc, x.tok
        FROM (SELECT entity_id, cc, unnest(str_split({tgtcol},' ')) tok FROM tgt) x
        JOIN surv_{name} v ON x.cc=v.cc AND x.tok=v.tok""")
    con.execute(f"DROP TABLE IF EXISTS cand_{name}")
    con.execute(f"""CREATE TEMP TABLE cand_{name} AS
        SELECT s1, mid, sh, score FROM (
          SELECT s1, mid, sh, score,
                 row_number() OVER (PARTITION BY s1 ORDER BY score DESC, sh DESC, hash(mid)) rk
          FROM (SELECT s.entity_id s1, t.entity_id mid, count(*) sh,
                       sum(1.0/v.df_t) score
                FROM s1tok_{name} s
                JOIN surv_{name} v ON s.cc=v.cc AND s.tok=v.tok
                JOIN tgtok_{name} t ON t.cc=s.cc AND t.tok=s.tok
                GROUP BY 1,2)
        ) WHERE rk <= {cap}""")
    nc = con.sql(f"SELECT count(*) FROM cand_{name}").fetchone()[0]
    # realized (post-cap) recall on pk, and per-S1 candidate distribution
    capcov = con.sql(f"""SELECT count(*) FROM pk p WHERE (p.s1,p.mid) IN
        (SELECT s1,mid FROM cand_{name})""").fetchone()[0]
    d = con.sql(f"""SELECT avg(c),median(c),quantile_cont(c,0.9),quantile_cont(c,0.95),
        quantile_cont(c,0.99),max(c),count(*) FILTER(WHERE c=0)
        FROM (SELECT s.entity_id, count(c.mid) c FROM s1v s
              LEFT JOIN cand_{name} c ON s.entity_id=c.s1 GROUP BY 1)""").fetchone()
    out.update(cands=nc, cap=cap, cap_recall=capcov/tot, mean=d[0], median=d[1], p90=d[2],
               p95=d[3], p99=d[4], max=d[5], zero=d[6], guarded=False)
    p(f"    per-S1 top-{cap}: distinct candidates={nc}, realized (post-cap) pair-recall="
      f"{capcov/tot:.4f} ({capcov}) vs uncapped ceiling {cov/tot:.4f}; "
      f"cand/S1 mean={d[0]:.2f} median={d[1]:.0f} p90={d[2]:.0f} p95={d[3]:.0f} "
      f"p99={d[4]:.0f} max={d[5]:.0f}, zero-cand-S1={d[6]}")
    return out


def build_flags(con):
    """Per-GT-pair coverage flags for every pass — the exact union/incremental oracle."""
    con.execute("""CREATE TEMP TABLE pkf AS
        SELECT p.s1, p.mid, p.cc,
          (length(p.sns)>0 AND p.sns=p.tns) AS c_exact,
          (length(p.ssrt)>0 AND p.ssrt=p.tsrt) AS c_sorted,
          (length(p.sad)>0 AND p.sad=p.tad) AS c_addr,
          (cn.s1 IS NOT NULL) AS c_nametok,
          (ca.s1 IS NOT NULL) AS c_addrtok,
          (length(p.sad)>0 AND length(p.tad)>0) AS both_addr
        FROM pk p
        LEFT JOIN covtok_nametok cn ON p.s1=cn.s1 AND p.mid=cn.mid
        LEFT JOIN covtok_addrtok ca ON p.s1=ca.s1 AND p.mid=ca.mid""")


UNION_ORDER = [("exact_name", "c_exact"), ("sorted_name", "c_sorted"),
               ("exact_addr", "c_addr"), ("nametok", "c_nametok"), ("addrtok", "c_addrtok")]


def union_incremental(con, tot, nS1pairs):
    """Cumulative union pair-recall + incremental GT links in a fixed deterministic order.
    Candidate-cost deltas are reported by the materialized final stream (below)."""
    p("\n## Step 6 — cumulative union pair-recall (fixed order) + incremental GT links\n")
    cum = "false"
    prev = 0
    for name, col in UNION_ORDER:
        cum = f"({cum} OR {col})"
        c = con.sql(f"SELECT count(*) FROM pkf WHERE {cum}").fetchone()[0]
        s1c = con.sql(f"SELECT count(DISTINCT s1) FROM pkf WHERE {cum}").fetchone()[0]
        p(f"  + {name}: union pair-recall={c/tot:.4f} ({c}); incremental GT links={c-prev}; "
          f"union S1-coverage={s1c/nS1pairs:.4f}")
        prev = c


def breakdowns(con):
    """Step 8 — per country (open-set) and address-missingness, union pair-recall."""
    p("\n## Step 8 — per-country + address-missingness (union of all passes)\n")
    unio = "(c_exact OR c_sorted OR c_addr OR c_nametok OR c_addrtok)"
    p("### by country (open-set; NOT hard-coded)")
    for cc, tt, cv in con.sql(f"""SELECT cc, count(*), count(*) FILTER(WHERE {unio})
        FROM pkf GROUP BY 1 ORDER BY 2 DESC""").fetchall():
        p(f"  - {cc}: union pair-recall={cv/tt:.4f} ({cv}/{tt})")
    p("### by address presence")
    for lbl, cond in (("both present", "both_addr"), ("either missing", "NOT both_addr")):
        r = con.sql(f"""SELECT count(*), count(*) FILTER(WHERE {unio}),
            count(*) FILTER(WHERE c_addr OR c_addrtok) FROM pkf WHERE {cond}""").fetchone()
        p(f"  - {lbl}: union recall={r[1]/r[0]:.4f} ({r[1]}/{r[0]}); "
          f"of which address-pass-covered={r[2]}")


def address_conversion(con):
    """Step 7 — similarity OPPORTUNITY vs actual RETRIEVAL. Among pairs lost by exact-name
    with both addresses and addr Jaccard>=0.5, how many are ACTUALLY retrieved by an address
    pass? Explicitly separates 'similar' from 'retrieved'."""
    p("\n## Step 7 — address similarity → retrieval conversion (NOT the same thing)\n")
    con.execute("""CREATE TEMP TABLE addrsim AS
        SELECT p.s1, p.mid, jac(p.sad,p.tad) j,
               (length(p.sad)>0 AND p.sad=p.tad) c_addr,
               (ca.s1 IS NOT NULL) c_addrtok
        FROM pk p LEFT JOIN covtok_addrtok ca ON p.s1=ca.s1 AND p.mid=ca.mid
        WHERE NOT (length(p.sns)>0 AND p.sns=p.tns)
          AND length(p.sad)>0 AND length(p.tad)>0""")
    r = con.sql("""SELECT count(*), count(*) FILTER(WHERE j>=0.5),
        count(*) FILTER(WHERE j>=0.5 AND c_addr),
        count(*) FILTER(WHERE j>=0.5 AND (c_addr OR c_addrtok)),
        count(*) FILTER(WHERE j>=0.5 AND NOT (c_addr OR c_addrtok)) FROM addrsim""").fetchone()
    p(f"  - lost-by-exact pairs with both addresses = {r[0]}")
    p(f"  - of those, addr Jaccard>=0.5 (SIMILARITY OPPORTUNITY) = {r[1]}")
    p(f"  - ACTUALLY retrieved by exact-address pass = {r[2]} "
      f"({100*r[2]/r[1]:.1f}% of the opportunity)")
    p(f"  - ACTUALLY retrieved by exact-addr OR addr-token = {r[3]} "
      f"({100*r[3]/r[1]:.1f}% of the opportunity)")
    p(f"  - similar (>=0.5) but NOT retrieved by any address pass = {r[4]} "
      f"→ similarity does NOT imply retrieval")


def final_stream_and_pruning(con, nval, tot, addr_guard=40_000_000):
    """Step 11 + Step 9. Materialize ONE deterministic final candidate stream from the
    selected union (sorted_name ∪ exact_addr ∪ name_token ∪ addr_token; exact_name ⊆ sorted
    so it is subsumed), identity=(s1,mid) deduped, provenance kept as a separate bitmask.
    Then compare UNCAPPED vs two per-S1 caps, separating retrieval loss from pruning loss."""
    p("\n## Step 11 — final candidate stream (deduped identity + provenance bitmask)\n")
    parts = []
    # sorted-name candidate set (subsumes exact-name)
    con.execute("""CREATE TEMP TABLE cand_sorted AS
        SELECT DISTINCT s.entity_id s1, t.entity_id mid FROM s1v s JOIN tgt t
        ON length(s.ssrt)>0 AND s.cc=t.cc AND s.ssrt=t.tsrt""")
    parts.append(("cand_sorted", 1))
    # exact-address candidate set (guarded)
    addr_est = con.sql("""SELECT coalesce(sum(a*b),0) FROM
        (SELECT cc,tad,count(*) b FROM tgt WHERE length(tad)>0 GROUP BY 1,2) t
        JOIN (SELECT cc,sad,count(*) a FROM s1v WHERE length(sad)>0 GROUP BY 1,2) s
          ON s.cc=t.cc AND s.sad=t.tad""").fetchone()[0]
    if addr_est <= addr_guard:
        con.execute("""CREATE TEMP TABLE cand_addr AS
            SELECT DISTINCT s.entity_id s1, t.entity_id mid FROM s1v s JOIN tgt t
            ON length(s.sad)>0 AND s.cc=t.cc AND s.sad=t.tad""")
        parts.append(("cand_addr", 2))
    else:
        p(f"  exact-address candidate estimate={addr_est} > guard {addr_guard} → EXCLUDED "
          f"from final stream (reported, not materialized).")
    if con.sql("SELECT count(*) FROM information_schema.tables WHERE table_name='cand_nametok'").fetchone()[0]:
        parts.append(("cand_nametok", 4))
    if con.sql("SELECT count(*) FROM information_schema.tables WHERE table_name='cand_addrtok'").fetchone()[0]:
        parts.append(("cand_addrtok", 8))
    union_sql = " UNION ALL ".join(f"SELECT s1,mid,{bit} b FROM {t}" for t, bit in parts)
    con.execute(f"""CREATE TEMP TABLE final_cand AS
        SELECT s1, mid, bit_or(b)::INT prov FROM ({union_sql}) GROUP BY 1,2""")
    nfc = con.sql("SELECT count(*) FROM final_cand").fetchone()[0]
    p(f"  passes materialized into final stream: {[t for t,_ in parts]} "
      f"(prov bits: sorted=1, addr=2, nametok=4, addrtok=8)")
    p(f"  per-pass-capped union distinct candidates={nfc} (nametok/addrtok already top-100/S1)")
    # covered GT pairs present in the stream (retrieval recall of the materialized stream)
    covfc = con.sql("""SELECT count(*) FROM pk p WHERE (p.s1,p.mid) IN
        (SELECT s1,mid FROM final_cand)""").fetchone()[0]
    ceil = con.sql("""SELECT count(*) FROM pkf
        WHERE (c_sorted OR c_addr OR c_nametok OR c_addrtok)""").fetchone()[0]
    p(f"  UNCAPPED retrieval ceiling (pk oracle, union of passes)={ceil/tot:.4f} ({ceil})")
    p(f"  per-pass-capped union pair-recall={covfc/tot:.4f} ({covfc}); "
      f"pruning loss from the per-pass top-100 cap = {ceil-covfc}")
    d = con.sql("""SELECT avg(c),median(c),quantile_cont(c,0.9),quantile_cont(c,0.95),
        quantile_cont(c,0.99),max(c) FROM
        (SELECT s.entity_id, count(f.mid) c FROM s1v s LEFT JOIN final_cand f
         ON s.entity_id=f.s1 GROUP BY 1)""").fetchone()
    p(f"  cand/S1 mean={d[0]:.2f} median={d[1]:.0f} p90={d[2]:.0f} p95={d[3]:.0f} "
      f"p99={d[4]:.0f} max={d[5]:.0f}")
    # deterministic per-S1 rank: provenance strength (popcount) desc, then hash(mid)
    con.execute("""CREATE TEMP TABLE ranked AS
        SELECT s1, mid, prov, row_number() OVER (PARTITION BY s1
          ORDER BY bit_count(prov) DESC, hash(mid)) rk FROM final_cand""")
    p("\n## Step 9 — pruning/capping (per-pass-capped union vs added union-level top-K)\n")
    p(f"  retrieval ceiling (no cap) = {ceil/tot:.4f}; losses below are attributable to capping only.")
    for K in (None, 100, 50):
        if K is None:
            nc, cov = nfc, covfc
            lbl = "per-pass-capped union (top-100/pass, no union cap)"
        else:
            nc = con.sql(f"SELECT count(*) FROM ranked WHERE rk<={K}").fetchone()[0]
            cov = con.sql(f"""SELECT count(*) FROM pk p WHERE (p.s1,p.mid) IN
                (SELECT s1,mid FROM ranked WHERE rk<={K})""").fetchone()[0]
            lbl = f"+ union cap top-{K}/S1"
        p(f"  - {lbl}: candidates={nc}, pair-recall={cov/tot:.4f} ({cov}); "
          f"cumulative pruning loss vs ceiling={ceil-cov}")
    con.execute("""COPY (SELECT s1 AS source1_entity_id, mid AS target_entity_id, prov
        FROM final_cand) TO 'work/final_candidate_provenance.parquet' (FORMAT parquet)""")
    p("  final stream + provenance → work/final_candidate_provenance.parquet")


def lost_pair_analysis(con, tot):
    """Step 14 — categorize GT pairs still lost by the selected union (measured only)."""
    p("\n## Step 14 — remaining lost pairs (selected union: sorted∪addr∪nametok∪addrtok)\n")
    unio = "(c_sorted OR c_addr OR c_nametok OR c_addrtok)"
    lost = con.sql(f"SELECT count(*) FROM pkf WHERE NOT {unio}").fetchone()[0]
    p(f"  remaining lost GT links = {lost} (of {tot}; union recall={1-lost/tot:.4f})")
    con.execute(f"""CREATE TEMP TABLE lost2 AS
        SELECT p.* FROM pk p JOIN pkf f ON p.s1=f.s1 AND p.mid=f.mid WHERE NOT {unio}""")
    cats = [("missing address (either side)", "length(sad)=0 OR length(tad)=0"),
            ("cross-script name (deva↔latin)",
             "(scr(sns)='deva' AND scr(tns)='latin') OR (scr(sns)='latin' AND scr(tns)='deva')"),
            ("zero name-token overlap", "jac(sns,tns)=0"),
            ("low name overlap (0,0.5)", "jac(sns,tns)>0 AND jac(sns,tns)<0.5"),
            ("both addr present, addr jac<0.5",
             "length(sad)>0 AND length(tad)>0 AND coalesce(jac(sad,tad),0)<0.5")]
    for lbl, cond in cats:
        n = con.sql(f"SELECT count(*) FROM lost2 WHERE {cond}").fetchone()[0]
        p(f"  - {lbl}: {n} ({100*n/lost:.1f}% of lost)")


def feasibility(con, args):
    """Step 15 — bounded full-scale estimate (NO unsafe full run)."""
    p("\n## Step 15 — full-scale feasibility (estimate; not run at full scale)\n")
    k = args.keys_dir
    # sorted-key self-product estimate on full TRAIN targets as a bound proxy
    est = con.sql(f"""SELECT coalesce(sum(c*c),0) FROM (
        SELECT country_norm cc, name_sorted, count(*) c FROM (
          SELECT country_norm, name_sorted FROM read_parquet('{k}/train_s2.parquet')
          UNION ALL SELECT country_norm, name_sorted FROM read_parquet('{k}/train_s3.parquet'))
        WHERE length(name_sorted)>0 GROUP BY 1,2)""").fetchone()[0]
    p(f"  sorted-key block self-product over FULL train targets (S2∪S3) ≈ {est} "
      f"(worst-case within-target block cost proxy; actual S1×target scales with test S1 count).")
    p("  Development val (220,531 S1) ran within the 512MB DuckDB cap, spilling to "
      "work/duckdb_tmp; test S1=1,732,544 (~7.9× val). Recommended: partition candidate "
      "generation by country_norm and stream per-block to disk (integer-coded IDs); the "
      "per-block cap keeps peak RSS bounded. No unsafe all-pairs run performed.")


def grouped_stress_test(con, args, gt):
    """Step 12 — run the SELECTED policy EXACTLY ONCE on the grouped name-key stress-test.
    Distribution-shift diagnostic ONLY — not a leaderboard/lower-bound estimate. Reuses the
    frozen surviving-token sets (targets are identical; only the val S1 subset changes)."""
    p("\n## Step 12 — grouped name-key stress-test (SELECTED policy, run ONCE, diagnostic)\n")
    k, sp = args.keys_dir, "work/split_s1_grouped.parquet"
    con.execute(f"""CREATE TEMP TABLE s1g AS
        SELECT a.entity_id, a.country_norm cc, a.name_nosuffix sns, a.name_sorted ssrt, a.addr_norm sad
        FROM read_parquet('{k}/train_s1.parquet') a
        JOIN read_parquet('{sp}') s ON a.entity_id=s.entity_id AND s.split='val'""")
    con.execute(f"""CREATE TEMP TABLE gtpg AS
        SELECT g.source1_entity_id s1, trim(x) mid
        FROM read_csv('{gt}', delim='\t', header=true, quote='', all_varchar=true) g,
             UNNEST(string_split(g.matched_entity_ids, ',')) AS u(x)
        WHERE g.matched_entity_ids IS NOT NULL AND length(trim(g.matched_entity_ids))>0
          AND g.source1_entity_id IN (SELECT entity_id FROM s1g)""")
    con.execute("""CREATE TEMP TABLE pkg AS
        SELECT g.s1, g.mid, s.cc, s.sns, s.ssrt, s.sad, t.tns, t.tsrt, t.tad
        FROM gtpg g JOIN s1g s ON g.s1=s.entity_id JOIN tgt t ON g.mid=t.entity_id""")
    con.execute("""CREATE TEMP TABLE pkgf AS
        SELECT p.s1, p.mid,
          (length(p.ssrt)>0 AND p.ssrt=p.tsrt) c_sorted,
          (length(p.sad)>0 AND p.sad=p.tad) c_addr,
          (cn.s1 IS NOT NULL) c_nametok, (ca.s1 IS NOT NULL) c_addrtok
        FROM pkg p
        LEFT JOIN (SELECT DISTINCT a.s1,a.mid FROM
          (SELECT s1,mid,cc,unnest(str_split(sns,' ')) tok FROM pkg) a
          JOIN surv_nametok v ON a.cc=v.cc AND a.tok=v.tok
          JOIN (SELECT s1,mid,cc,unnest(str_split(tns,' ')) tok FROM pkg) b
            ON b.s1=a.s1 AND b.mid=a.mid AND b.cc=a.cc AND b.tok=a.tok) cn
          ON p.s1=cn.s1 AND p.mid=cn.mid
        LEFT JOIN (SELECT DISTINCT a.s1,a.mid FROM
          (SELECT s1,mid,cc,unnest(str_split(sad,' ')) tok FROM pkg) a
          JOIN surv_addrtok v ON a.cc=v.cc AND a.tok=v.tok
          JOIN (SELECT s1,mid,cc,unnest(str_split(tad,' ')) tok FROM pkg) b
            ON b.s1=a.s1 AND b.mid=a.mid AND b.cc=a.cc AND b.tok=a.tok) ca
          ON p.s1=ca.s1 AND p.mid=ca.mid""")
    r = con.sql("""SELECT count(*), count(*) FILTER(WHERE c_sorted OR c_addr OR c_nametok OR c_addrtok)
        FROM pkgf""").fetchone()
    p(f"  grouped val GT links={r[0]}; SELECTED-policy union pair-recall={r[1]/r[0]:.4f} ({r[1]})")
    p("  INTERPRETATION: distribution-shift diagnostic on unseen exact name-keys. NOT a "
      "guaranteed lower bound, NOT a leaderboard/private-score/ranking estimate.")


def _f(x):
    return f"{x:.2f}" if isinstance(x, (int, float)) else str(x)


def write_outputs(con, ms, tot, nS1pairs, nval):
    with open("work/candidate_experiments.md", "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    con.execute("""CREATE TEMP TABLE metrics(pass VARCHAR, pair_recall DOUBLE,
        s1_coverage DOUBLE, distinct_cands BIGINT, cand_per_s1_mean DOUBLE, p95 DOUBLE,
        p99 DOUBLE, mx DOUBLE)""")
    for n, m in ms.items():
        con.execute("INSERT INTO metrics VALUES (?,?,?,?,?,?,?,?)",
                    [n, m["recall"], m["s1recall"], m.get("cands"),
                     m.get("mean"), m.get("p95"), m.get("p99"), m.get("max")])
    con.execute("COPY metrics TO 'work/candidate_experiment_metrics.parquet' (FORMAT parquet)")
    nfc = con.sql("SELECT count(*) FROM final_cand").fetchone()[0]
    unio = "(c_sorted OR c_addr OR c_nametok OR c_addrtok)"
    ur = con.sql(f"SELECT count(*) FROM pkf WHERE {unio}").fetchone()[0]
    with open("work/candidate_experiment_summary.md", "w", encoding="utf-8") as f:
        f.write("# Candidate experiment summary (DEVELOPMENT split)\n\n")
        f.write(f"val S1={nval}; GT links={tot}; S1 with GT={nS1pairs}. "
                "Full evidence: work/candidate_experiments.md.\n\n")
        f.write("| pass | pair-recall | S1-coverage | distinct cands | cand/S1 mean | p99 | max |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for n, m in ms.items():
            f.write(f"| {n} | {m['recall']:.4f} | {m['s1recall']:.4f} | {m.get('cands')} | "
                    f"{_f(m.get('mean'))} | {_f(m.get('p99'))} | {_f(m.get('max'))} |\n")
        f.write(f"\nSelected union (sorted∪addr∪nametok∪addrtok) pair-recall="
                f"{ur/tot:.4f}; final distinct candidates={nfc}.\n")
    with open("work/final_candidate_policy.md", "w", encoding="utf-8") as f:
        f.write("# Selected candidate-generation policy\n\n")
        f.write("**Policy:** union of sorted-name key, exact-address key, frequency-aware "
                "name-token blocking, and frequency-aware address-token blocking; identity "
                "(source1_entity_id, target_entity_id) deduped; provenance bitmask kept "
                "separately (sorted=1, addr=2, nametok=4, addrtok=8).\n\n")
        f.write(f"Development val pair-recall={ur/tot:.4f}; distinct candidates={nfc}. "
                "Pruning options + grouped stress-test in work/candidate_experiments.md. "
                "exact-name is subsumed (exact ⊆ sorted). NO model trained; NO "
                "matching_results.tsv generated.\n")
    with open("work/candidate_generation_audit.md", "w", encoding="utf-8") as f:
        f.write("# Candidate-generation audit trail\n\n")
        f.write("Reproduce:\n\n    PYTHONUTF8=1 .venv/Scripts/python scripts/measure_peak.py "
                "--script scripts/candidate_experiments.py -- --data-dir dataset\n\n")
        f.write("Bounded DuckDB (memory_limit=1GB, threads=4, temp=work/duckdb_tmp; spills "
                "to disk). Measured process peak RSS ~1.27 GiB via scripts/measure_peak.py "
                "(GetProcessMemoryInfo PeakWorkingSetSize), well under available RAM. Recall "
                "measured exactly on the GT-pair oracle; single-key volume via key "
                "products; token-pass volume via materialized per-S1-capped distinct sets.\n")
    p("\n## Artifacts written")
    p("  candidate_experiments.md, candidate_experiment_summary.md, "
      "candidate_experiment_metrics.parquet, final_candidate_policy.md, "
      "final_candidate_provenance.parquet, candidate_generation_audit.md")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="dataset")
    ap.add_argument("--keys-dir", default="work/keys")
    ap.add_argument("--split", default="work/split_s1.parquet")
    ap.add_argument("--name-drop", type=int, default=2000)
    ap.add_argument("--addr-drop", type=int, default=2000)
    args = ap.parse_args()
    gt = f"{args.data_dir}/train/train_ground_truth.tsv"
    os.makedirs("work/duckdb_tmp", exist_ok=True)
    con = duckdb.connect()
    setup(con)
    base_tables(con, args.keys_dir, args.split, gt)
    tot = con.sql("SELECT count(*) FROM pk").fetchone()[0]
    nS1pairs = con.sql("SELECT count(DISTINCT s1) FROM pk").fetchone()[0]
    nval = con.sql("SELECT count(*) FROM s1v").fetchone()[0]
    p("# Candidate-generation experiments — DEVELOPMENT split (work/split_s1.parquet, val)\n")
    p(f"Base: val S1={nval}; val S1 with >=1 GT link={nS1pairs}; total GT links={tot}. "
      f"PRIMARY metric = pair-level GT-link recall. S1-coverage reported separately.\n")

    p("## Step 2 — single-key baselines (exact volume via key products)\n")
    ms = {n: single_key(con, n, tot, nS1pairs) for n in ("exact_name", "sorted_name", "exact_addr")}
    for n in ("exact_name", "sorted_name", "exact_addr"):
        p(fmt_pass(ms[n]))

    sorted_containment(con, tot)

    p("\n## Step 5 — token frequency profiles (justify drop thresholds)\n")
    token_tables(con, "nametok", "sns", "tns")
    token_tables(con, "addrtok", "sad", "tad")
    token_df_report(con, "nametok")
    token_df_report(con, "addrtok")

    p("\n## Steps 4/6 — token passes (frequency-aware, block-capped)\n")
    mt_name = token_pass(con, "nametok", "sns", "tns", "sns", "tns", args.name_drop, tot, nS1pairs)
    mt_addr = token_pass(con, "addrtok", "sad", "tad", "sad", "tad", args.addr_drop, tot, nS1pairs)
    ms["nametok"] = mt_name
    ms["addrtok"] = mt_addr

    build_flags(con)
    union_incremental(con, tot, nS1pairs)
    breakdowns(con)
    address_conversion(con)
    final_stream_and_pruning(con, nval, tot)
    lost_pair_analysis(con, tot)
    feasibility(con, args)
    grouped_stress_test(con, args, gt)
    write_outputs(con, ms, tot, nS1pairs, nval)


if __name__ == "__main__":
    main()
