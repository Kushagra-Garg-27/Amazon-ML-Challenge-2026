"""Reproducible, label-free candidate-policy experiments on the frozen val split.

GT is used only for audits after candidate ranking. Run stages separately so DuckDB
can release all buffers between large joins. All outputs are under work/freeze_gate.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import duckdb

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT = Path("work/freeze_gate")
TMP = OUT / "tmp"


class MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
                ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended", ctypes.c_ulonglong)]


def free_ram() -> int:
    s = MemoryStatus()
    s.length = ctypes.sizeof(s)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)):
        raise OSError("GlobalMemoryStatusEx failed")
    return s.avail_phys


class Guard:
    def __init__(self, c: duckdb.DuckDBPyConnection):
        self.c = c
        self.stop = threading.Event()
        self.violated = ""
        self.peak_temp = 0
        self.thread = threading.Thread(target=self.poll, daemon=True)

    def __enter__(self):
        ram, disk = free_ram(), shutil.disk_usage(TMP).free
        # Prior observed input-token join estimate: 127.8M name + 247.3M
        # address occurrences, before pair aggregation. Process one pass at a time.
        print(f"guard free_ram={ram:,} free_temp_disk={disk:,} "
              "duckdb=800MB threads=1 estimated_join_rows=375077121", flush=True)
        if ram < 1_350_000_000 or disk < 15_000_000_000:
            raise RuntimeError("Insufficient free RAM or temp disk for guarded run")
        self.thread.start()
        return self

    def poll(self):
        while not self.stop.wait(0.5):
            try:
                growth = sum(p.stat().st_size for p in TMP.rglob("*") if p.is_file())
                self.peak_temp = max(self.peak_temp, growth)
                ram, disk = free_ram(), shutil.disk_usage(TMP).free
                if ram < 500_000_000 or disk < 10_000_000_000:
                    self.violated = f"Resource guard tripped: free_ram={ram}, free_disk={disk}"
                    self.c.interrupt()
                    return
            except Exception as exc:
                self.violated = f"Resource monitor failed: {exc}"
                self.c.interrupt()
                return

    def __exit__(self, typ, val, tb):
        self.stop.set()
        self.thread.join(timeout=3)
        print(f"peak_temp_bytes={self.peak_temp:,} guard={self.violated or 'OK'}", flush=True)
        if self.violated and typ is None:
            raise RuntimeError(self.violated)


def conn() -> duckdb.DuckDBPyConnection:
    OUT.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.execute("SET memory_limit='800MB'; SET threads=1; SET preserve_insertion_order=false")
    c.execute(f"SET temp_directory='{TMP.as_posix()}'")
    return c


def copy(c, sql: str, name: str) -> None:
    target = OUT / f"{name}.parquet"
    partial = OUT / f"{name}.partial.parquet"
    partial.unlink(missing_ok=True)
    c.execute(f"COPY ({sql}) TO '{partial.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    os.replace(partial, target)
    print(f"{name}: {c.sql(f'SELECT count(*) FROM read_parquet(\'{target.as_posix()}\')').fetchone()[0]:,} rows, {target.stat().st_size:,} bytes", flush=True)


def source_sql(source: str, cols: str) -> str:
    return f"SELECT {cols} FROM read_parquet('work/keys/train_{source}.parquet')"


def build_df(c, field: str) -> None:
    col = "name_nosuffix" if field == "name" else "addr_norm"
    both = " UNION ALL ".join(source_sql(s, f"entity_id, country_norm cc, {col} val") for s in ("s2", "s3"))
    copy(c, f"""SELECT cc,tok,count(*)::INT df FROM (
        SELECT cc,unnest(list_distinct(str_split(val,' '))) tok FROM ({both})
    ) WHERE length(tok)>0 GROUP BY 1,2""", f"df_{field}")


def audit() -> None:
    c = conn()
    start = time.time()
    with Guard(c):
        audit_inner(c)
    print(f"audit wall_seconds={time.time()-start:.1f}", flush=True)
    c.close()


def audit_inner(c) -> None:
    for field in ("name", "addr"):
        if not (OUT / f"df_{field}.parquet").exists():
            print(f"Building target {field} DF", flush=True)
            build_df(c, field)
    print("Building direct GT join", flush=True)
    c.execute("""CREATE TEMP TABLE s1v AS SELECT a.entity_id,a.country_norm cc,
        a.name_nosuffix sns,a.name_sorted ssrt,a.addr_norm sad
        FROM read_parquet('work/keys/train_s1.parquet') a
        JOIN read_parquet('work/split_s1.parquet') s
          ON a.entity_id=s.entity_id AND s.split='val'""")
    copy(c, """SELECT g.source1_entity_id s1,trim(x) mid FROM
        read_csv('dataset/train/train_ground_truth.tsv',delim='\t',header=true,quote='',all_varchar=true) g
        JOIN s1v s ON g.source1_entity_id=s.entity_id,
        UNNEST(string_split(g.matched_entity_ids,',')) u(x)
        WHERE g.matched_entity_ids IS NOT NULL AND length(trim(g.matched_entity_ids))>0""", "gt")
    target = " UNION ALL ".join(source_sql(s, "entity_id,country_norm cc,name_nosuffix tns,name_sorted tsrt,addr_norm tad") for s in ("s2", "s3"))
    copy(c, f"""SELECT g.s1,g.mid,s.cc,s.sns,s.ssrt,s.sad,t.tns,t.tsrt,t.tad
       FROM read_parquet('{(OUT / 'gt.parquet').as_posix()}') g
       JOIN s1v s ON g.s1=s.entity_id
       JOIN ({target}) t ON g.mid=t.entity_id""", "gt_detail")
    # Count overlap directly on GT pairs. The DF join is the exact eligibility
    # definition used by the original candidate generator (country, token, df<=2000).
    for field, left, right in (("name", "sns", "tns"), ("addr", "sad", "tad")):
        copy(c, f"""SELECT p.s1,p.mid,
            count(*)::INT raw_overlap,
            count(*) FILTER (WHERE d.df<=2000)::INT eligible_overlap
            FROM (SELECT s1,mid,cc,unnest(str_split({left},' ')) tok
                  FROM read_parquet('{(OUT / 'gt_detail.parquet').as_posix()}')) p
            JOIN (SELECT s1,mid,unnest(str_split({right},' ')) tok
                  FROM read_parquet('{(OUT / 'gt_detail.parquet').as_posix()}')) t
              ON p.s1=t.s1 AND p.mid=t.mid AND p.tok=t.tok
            LEFT JOIN read_parquet('{(OUT / f'df_{field}.parquet').as_posix()}') d
              ON p.cc=d.cc AND p.tok=d.tok
            WHERE length(p.tok)>0 GROUP BY 1,2""", f"gt_overlap_{field}")
    copy(c, f"""SELECT p.*,
        (length(p.ssrt)>0 AND p.ssrt=p.tsrt) sorted_eligible,
        (length(p.sad)>0 AND p.sad=p.tad) addr_eligible,
        coalesce(n.raw_overlap,0)::INT raw_name_overlap,
        coalesce(a.raw_overlap,0)::INT raw_addr_overlap,
        coalesce(n.eligible_overlap,0)::INT eligible_name_overlap,
        coalesce(a.eligible_overlap,0)::INT eligible_addr_overlap,
        (length(p.ssrt)>0 AND p.ssrt=p.tsrt OR
         length(p.sad)>0 AND p.sad=p.tad OR
         coalesce(n.eligible_overlap,0)>0 OR coalesce(a.eligible_overlap,0)>0) eligible
        FROM read_parquet('{(OUT / 'gt_detail.parquet').as_posix()}') p
        LEFT JOIN read_parquet('{(OUT / 'gt_overlap_name.parquet').as_posix()}') n USING (s1,mid)
        LEFT JOIN read_parquet('{(OUT / 'gt_overlap_addr.parquet').as_posix()}') a USING (s1,mid)""", "oracle")
    art = "work/final_candidate_provenance.parquet"
    copy(c, f"""SELECT o.* FROM read_parquet('{(OUT / 'oracle.parquet').as_posix()}') o
         ANTI JOIN read_parquet('{art}') a
         ON o.s1=a.source1_entity_id AND o.mid=a.target_entity_id
         WHERE NOT o.eligible""", "key_ineligible_loss")
    copy(c, f"""SELECT o.* FROM read_parquet('{(OUT / 'oracle.parquet').as_posix()}') o
         ANTI JOIN read_parquet('{art}') a
         ON o.s1=a.source1_entity_id AND o.mid=a.target_entity_id
         WHERE o.eligible""", "cap_ranking_loss")


def rank(field_filter: str | None = None, country_filter: str | None = None,
         shard_filter: int | None = None) -> None:
    c = conn()
    t0 = time.time()
    with Guard(c):
        c.execute("""CREATE TEMP TABLE s1v AS SELECT a.entity_id,a.country_norm cc,
            a.name_nosuffix sns,a.addr_norm sad
            FROM read_parquet('work/keys/train_s1.parquet') a
            JOIN read_parquet('work/split_s1.parquet') s
              ON a.entity_id=s.entity_id AND s.split='val'""")
        countries = [r[0] for r in c.sql("SELECT DISTINCT cc FROM s1v ORDER BY 1").fetchall()]
        for field, col in (("name", "name_nosuffix"), ("addr", "addr_norm")):
            if field_filter and field != field_filter:
                continue
            scol = "sns" if field == "name" else "sad"
            for country in countries:
                if country_filter and country != country_filter:
                    continue
                nshards = 1 if field == "name" else 4
                for shard in range(nshards):
                    if shard_filter is not None and shard != shard_filter:
                        continue
                    fname = f"rank_{field}_{country}" if nshards == 1 else f"rank_{field}_{country}_{shard}"
                    if (OUT / f"{fname}.parquet").exists():
                        print(f"Skipping existing {fname}", flush=True)
                        continue
                    print(f"Ranking {field} {country} shard={shard}/{nshards}", flush=True)
                    c.execute("DROP TABLE IF EXISTS s1tok")
                    c.execute("DROP TABLE IF EXISTS tgtok")
                    c.execute("DROP TABLE IF EXISTS scores")
                    c.execute(f"""CREATE TEMP TABLE s1tok AS
                        SELECT s.entity_id s1,s.cc,unnest(list_distinct(str_split(s.{scol},' '))) tok
                        FROM s1v s WHERE s.cc=? AND hash(s.entity_id)%?=?""", [country,nshards,shard])
                    target_union = " UNION ALL ".join(
                    f"SELECT entity_id,country_norm cc,{col} val FROM "
                    f"read_parquet('work/keys/train_{source}.parquet') "
                    f"WHERE country_norm='{country}'" for source in ("s2", "s3"))
                    c.execute(f"""CREATE TEMP TABLE tgtok AS
                    SELECT x.entity_id mid,x.cc,x.tok FROM (
                        SELECT entity_id,cc,unnest(list_distinct(str_split(val,' '))) tok FROM (
                            {target_union}
                        )
                    ) x JOIN read_parquet('{(OUT / f'df_{field}.parquet').as_posix()}') d
                    ON x.cc=d.cc AND x.tok=d.tok AND d.df<=2000
                    JOIN (SELECT DISTINCT tok FROM s1tok WHERE length(tok)>0) st ON x.tok=st.tok""")
                    n_s = c.sql("SELECT count(*) FROM s1tok WHERE length(tok)>0").fetchone()[0]
                    n_t = c.sql("SELECT count(*) FROM tgtok").fetchone()[0]
                    est = c.execute(f"""SELECT coalesce(sum(sd.n*d.df),0) FROM
                    (SELECT tok,count(*) n FROM s1tok WHERE length(tok)>0 GROUP BY 1) sd
                    JOIN read_parquet('{(OUT / f'df_{field}.parquet').as_posix()}') d
                    ON d.cc=? AND sd.tok=d.tok AND d.df<=2000""", [country]).fetchone()[0]
                    print(f"  s1_tokens={n_s:,} target_tokens={n_t:,} estimated_join_rows={est:,}", flush=True)
                    if est > 75_000_000:
                        raise RuntimeError(f"Join estimate {est} exceeds partition guard")
                    c.execute(f"""CREATE TEMP TABLE scores AS SELECT s.s1,t.mid,
                    count(*)::INT sh,sum(1.0/d.df) score FROM s1tok s
                    JOIN tgtok t ON s.cc=t.cc AND s.tok=t.tok
                    JOIN read_parquet('{(OUT / f'df_{field}.parquet').as_posix()}') d
                      ON s.cc=d.cc AND s.tok=d.tok
                    WHERE length(s.tok)>0 GROUP BY 1,2""")
                    print(f"  distinct_scored_pairs={c.sql('SELECT count(*) FROM scores').fetchone()[0]:,}", flush=True)
                    copy(c, f"""SELECT r.s1,r.mid,r.sh,r.score,r.rjoint,r.rsource FROM (
                    SELECT s1,mid,sh,score,
                    row_number() OVER (PARTITION BY s1 ORDER BY score DESC,sh DESC,hash(mid),mid)::INT rjoint,
                    row_number() OVER (PARTITION BY s1,substr(mid,1,2)
                                       ORDER BY score DESC,sh DESC,hash(mid),mid)::INT rsource
                    FROM scores) r
                    LEFT JOIN read_parquet('{(OUT / 'cap_ranking_loss.parquet').as_posix()}') l
                    ON r.s1=l.s1 AND r.mid=l.mid
                    WHERE r.rsource<=200 OR l.s1 IS NOT NULL""", fname)
                    c.execute("DROP TABLE scores")
                    c.execute("DROP TABLE tgtok")
                    c.execute("DROP TABLE s1tok")
    c.close()
    print(f"rank wall_seconds={time.time()-t0:.1f}", flush=True)


POLICIES = {
    "joint_100": "rjoint<=100",
    "joint_150": "rjoint<=150",
    "joint_200": "rjoint<=200",
    "source_50_50": "(mid LIKE 'S2-%' AND rsource<=50) OR (mid LIKE 'S3-%' AND rsource<=50)",
    "source_75_75": "(mid LIKE 'S2-%' AND rsource<=75) OR (mid LIKE 'S3-%' AND rsource<=75)",
    # Baseline source mix is 49.14% S2, 50.86% S3. Fixed 49/51 slots
    # reflect observed candidate availability without using GT labels.
    "source_49_51": "(mid LIKE 'S2-%' AND rsource<=49) OR (mid LIKE 'S3-%' AND rsource<=51)",
    "evidence_joint_75": "rjoint<=75",
    "evidence_joint_100": "rjoint<=100",
    "evidence_source_50_50": "(mid LIKE 'S2-%' AND rsource<=50) OR (mid LIKE 'S3-%' AND rsource<=50)",
    "evidence_source_75_75": "(mid LIKE 'S2-%' AND rsource<=75) OR (mid LIKE 'S3-%' AND rsource<=75)",
    "evidence_source_37_38": "(mid LIKE 'S2-%' AND rsource<=37) OR (mid LIKE 'S3-%' AND rsource<=38)",
}


def policy(name: str) -> None:
    c = conn()
    t0 = time.time()
    with Guard(c) as guard:
        if not (OUT / "sorted.parquet").exists() or not (OUT / "exact_addr.parquet").exists():
            c.execute("""CREATE TEMP TABLE s1v AS SELECT a.entity_id,a.country_norm cc,
                a.name_sorted ssrt,a.addr_norm sad
                FROM read_parquet('work/keys/train_s1.parquet') a
                JOIN read_parquet('work/split_s1.parquet') s
                  ON a.entity_id=s.entity_id AND s.split='val'""")
            tgt = " UNION ALL ".join(source_sql(s, "entity_id,country_norm cc,name_sorted tsrt,addr_norm tad") for s in ("s2", "s3"))
            if not (OUT / "sorted.parquet").exists():
                copy(c, f"""SELECT s.entity_id s1,t.entity_id mid FROM s1v s JOIN ({tgt}) t
                    ON s.cc=t.cc AND length(s.ssrt)>0 AND s.ssrt=t.tsrt""", "sorted")
            if not (OUT / "exact_addr.parquet").exists():
                copy(c, f"""SELECT s.entity_id s1,t.entity_id mid FROM s1v s JOIN ({tgt}) t
                    ON s.cc=t.cc AND length(s.sad)>0 AND s.sad=t.tad""", "exact_addr")
            c.execute("DROP TABLE s1v")
        prefix = "evidence_" if name.startswith("evidence_") else ""
        name_files = sorted(OUT.glob(f"rank_{prefix}name_*.parquet"))
        addr_files = sorted(OUT.glob(f"rank_{prefix}addr_*.parquet"))
        expected_name = 64 if prefix else 2
        expected_addr = 64 if prefix else 8
        if len(name_files) != expected_name or len(addr_files) != expected_addr:
            raise RuntimeError(f"Expected {expected_name} name and {expected_addr} address rank partitions")
        cond = POLICIES[name]
        names = ",".join("'" + f.as_posix() + "'" for f in name_files)
        addrs = ",".join("'" + f.as_posix() + "'" for f in addr_files)
        parts = []
        for shard in range(16):
            hash_filter = f"hash(s1)%16={shard}"
            pieces = [
                f"SELECT s1,mid,1 b FROM read_parquet('{(OUT / 'sorted.parquet').as_posix()}') WHERE {hash_filter}",
                f"SELECT s1,mid,2 b FROM read_parquet('{(OUT / 'exact_addr.parquet').as_posix()}') WHERE {hash_filter}",
                f"SELECT s1,mid,4 b FROM read_parquet([{names}]) WHERE ({cond}) AND {hash_filter}",
                f"SELECT s1,mid,8 b FROM read_parquet([{addrs}]) WHERE ({cond}) AND {hash_filter}",
            ]
            union = " UNION ALL ".join(pieces)
            part_name = f"{name}_part{shard}"
            if not (OUT / f"{part_name}.parquet").exists():
                copy(c, f"""SELECT s1 source1_entity_id,mid target_entity_id,
                    bit_or(b)::UTINYINT prov FROM ({union}) GROUP BY 1,2""", part_name)
            else:
                print(f"Reusing {part_name}", flush=True)
            parts.append(OUT / f"{part_name}.parquet")
        part_paths = ",".join("'" + x.as_posix() + "'" for x in parts)
        copy(c, f"SELECT * FROM read_parquet([{part_paths}])", name)
        metrics = evaluate_policy(c, name)
        if name == "joint_100":
            reconstructed = (OUT / "joint_100.parquet").as_posix()
            canonical = "work/final_candidate_provenance.parquet"
            metrics["candidate_pairs_new_vs_canonical"] = c.sql(f"""SELECT count(*) FROM
                read_parquet('{reconstructed}') n ANTI JOIN read_parquet('{canonical}') b
                ON n.source1_entity_id=b.source1_entity_id AND n.target_entity_id=b.target_entity_id""").fetchone()[0]
            metrics["candidate_pairs_missing_vs_canonical"] = c.sql(f"""SELECT count(*) FROM
                read_parquet('{canonical}') b ANTI JOIN read_parquet('{reconstructed}') n
                ON n.source1_entity_id=b.source1_entity_id AND n.target_entity_id=b.target_entity_id""").fetchone()[0]
        metrics.update(policy=name, wall_seconds=round(time.time()-t0, 2),
                       artifact_bytes=(OUT / f"{name}.parquet").stat().st_size,
                       peak_temp_bytes=guard.peak_temp)
        (OUT / f"{name}.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        print(json.dumps(metrics, indent=2), flush=True)
    c.close()


def evaluate_policy(c, name: str, art_override: str | None = None) -> dict:
    art = art_override or (OUT / f"{name}.parquet").as_posix()
    gt = (OUT / "gt.parquet").as_posix()
    base = "work/final_candidate_provenance.parquet"
    c.execute(f"""CREATE TEMP TABLE hits AS SELECT g.s1,g.mid
        FROM read_parquet('{gt}') g JOIN read_parquet('{art}') a
        ON g.s1=a.source1_entity_id AND g.mid=a.target_entity_id""")
    c.execute(f"""CREATE TEMP TABLE base_hits AS SELECT g.s1,g.mid
        FROM read_parquet('{gt}') g JOIN read_parquet('{base}') a
        ON g.s1=a.source1_entity_id AND g.mid=a.target_entity_id""")
    n = c.sql(f"SELECT count(*) FROM read_parquet('{art}')").fetchone()[0]
    gt_n = c.sql(f"SELECT count(*) FROM read_parquet('{gt}')").fetchone()[0]
    rec = c.sql("SELECT count(*) FROM hits").fetchone()[0]
    gain = c.sql("SELECT count(*) FROM hits ANTI JOIN base_hits USING (s1,mid)").fetchone()[0]
    loss = c.sql("SELECT count(*) FROM base_hits ANTI JOIN hits USING (s1,mid)").fetchone()[0]
    gt_intersection = c.sql("SELECT count(*) FROM hits JOIN base_hits USING(s1,mid)").fetchone()[0]
    s1_recovered = c.sql("SELECT count(DISTINCT s1) FROM hits").fetchone()[0]
    s1_gt = c.sql(f"SELECT count(DISTINCT s1) FROM read_parquet('{gt}')").fetchone()[0]
    candidate_intersection = c.sql(f"""SELECT count(*) FROM read_parquet('{art}') p
        JOIN read_parquet('{base}') b ON p.source1_entity_id=b.source1_entity_id
        AND p.target_entity_id=b.target_entity_id""").fetchone()[0]
    candidate_added = n - candidate_intersection
    base_n = c.sql(f"SELECT count(*) FROM read_parquet('{base}')").fetchone()[0]
    candidate_removed = base_n - candidate_intersection
    src = c.sql(f"""SELECT substr(g.mid,1,2),count(*),count(h.mid) FROM
        read_parquet('{gt}') g LEFT JOIN hits h USING(s1,mid) GROUP BY 1 ORDER BY 1""").fetchall()
    candidates_by_source = dict(c.sql(f"""SELECT substr(target_entity_id,1,2),count(*)
        FROM read_parquet('{art}') GROUP BY 1 ORDER BY 1""").fetchall())
    oracle = (OUT / "oracle.parquet").as_posix()
    country = {cc: dict(total=t,recovered=r,recall=r/t) for cc,t,r in c.sql(f"""SELECT o.cc,
        count(*),count(h.mid) FROM read_parquet('{oracle}') o LEFT JOIN hits h USING(s1,mid)
        GROUP BY 1 ORDER BY 1""").fetchall()}
    missingness = {k: dict(total=t,recovered=r,recall=r/t) for k,t,r in c.sql(f"""SELECT
        CASE WHEN length(o.sad)>0 AND length(o.tad)>0 THEN 'both_present'
             ELSE 'either_missing' END k,count(*),count(h.mid)
        FROM read_parquet('{oracle}') o LEFT JOIN hits h USING(s1,mid)
        GROUP BY 1 ORDER BY 1""").fetchall()}
    c.execute(f"""CREATE TEMP TABLE per_s1 AS SELECT v.entity_id s1,
        coalesce(a.n,0)::INT n,coalesce(a.s2,0)::INT s2,coalesce(a.s3,0)::INT s3
        FROM read_parquet('work/split_s1.parquet') v
        LEFT JOIN (SELECT source1_entity_id s1,count(*) n,
            count(*) FILTER(WHERE target_entity_id LIKE 'S2-%') s2,
            count(*) FILTER(WHERE target_entity_id LIKE 'S3-%') s3
            FROM read_parquet('{art}') GROUP BY 1) a ON v.entity_id=a.s1
        WHERE v.split='val'""")
    dist = c.sql("""SELECT avg(n),median(n),quantile_cont(n,.90),quantile_cont(n,.95),quantile_cont(n,.99),max(n),
        count(*) FILTER(WHERE n=0),
        count(*) FILTER(WHERE s2>0 AND s3=0),count(*) FILTER(WHERE s3>0 AND s2=0)
        FROM per_s1""").fetchone()
    return dict(candidates=n, gt_total=gt_n, recovered=rec, pair_recall=rec/gt_n,
                s1_recovered=s1_recovered,s1_gt=s1_gt,s1_coverage=s1_recovered/s1_gt,
                candidate_intersection=candidate_intersection,candidate_added=candidate_added,
                candidate_removed=candidate_removed,gt_intersection=gt_intersection,
                gained=gain, lost=loss, net=gain-loss,
                source={s: dict(total=t,recovered=r,recall=r/t) for s,t,r in src},
                country=country,address_missingness=missingness,
                candidates_by_source=candidates_by_source,
                mean=dist[0],median=dist[1],p90=dist[2],p95=dist[3],p99=dist[4],max=dist[5],
                zero_candidate_s1=dist[6],only_s2_s1=dist[7],only_s3_s1=dist[8])


def metrics(name: str) -> None:
    target = OUT / f"{name}.parquet"
    if not target.exists():
        raise FileNotFoundError(target)
    c = conn()
    t0 = time.time()
    with Guard(c) as guard:
        m = evaluate_policy(c, name)
        m.update(policy=name,metrics_wall_seconds=round(time.time()-t0,2),
                 artifact_bytes=target.stat().st_size,metrics_peak_temp_bytes=guard.peak_temp)
        (OUT / f"{name}.metrics.json").write_text(json.dumps(m,indent=2),encoding="utf-8")
        print(json.dumps(m,indent=2),flush=True)
    c.close()


def compare(left: str, right: str) -> None:
    """Report right relative to left using direct pair joins."""
    lp, rp = OUT/f"{left}.parquet", OUT/f"{right}.parquet"
    if left == "current_joint_100":
        lp = Path("work/final_candidate_provenance.parquet")
    if right == "current_joint_100":
        rp = Path("work/final_candidate_provenance.parquet")
    if not lp.exists() or not rp.exists():
        raise FileNotFoundError((lp,rp))
    c=conn()
    with Guard(c):
        gt=(OUT/"gt.parquet").as_posix()
        l=lp.as_posix(); r=rp.as_posix()
        candidate_intersection=c.sql(f"""SELECT count(*) FROM read_parquet('{l}') a
            JOIN read_parquet('{r}') b ON a.source1_entity_id=b.source1_entity_id
            AND a.target_entity_id=b.target_entity_id""").fetchone()[0]
        ln=c.sql(f"SELECT count(*) FROM read_parquet('{l}')").fetchone()[0]
        rn=c.sql(f"SELECT count(*) FROM read_parquet('{r}')").fetchone()[0]
        c.execute(f"""CREATE TEMP TABLE lh AS SELECT g.* FROM read_parquet('{gt}') g
            JOIN read_parquet('{l}') a ON g.s1=a.source1_entity_id AND g.mid=a.target_entity_id""")
        c.execute(f"""CREATE TEMP TABLE rh AS SELECT g.* FROM read_parquet('{gt}') g
            JOIN read_parquet('{r}') a ON g.s1=a.source1_entity_id AND g.mid=a.target_entity_id""")
        gh=c.sql("SELECT count(*) FROM lh JOIN rh USING(s1,mid)").fetchone()[0]
        lhn=c.sql("SELECT count(*) FROM lh").fetchone()[0]; rhn=c.sql("SELECT count(*) FROM rh").fetchone()[0]
        out=dict(left=left,right=right,candidate_intersection=candidate_intersection,
                 candidates_added=rn-candidate_intersection,candidates_removed=ln-candidate_intersection,
                 gt_intersection=gh,gt_gained=rhn-gh,gt_lost=lhn-gh,net_gt=rhn-lhn)
        filename=f"compare_{left}_to_{right}.json"
        (OUT/filename).write_text(json.dumps(out,indent=2),encoding="utf-8")
        print(json.dumps(out,indent=2),flush=True)
    c.close()


def baseline() -> None:
    c = conn()
    t0 = time.time()
    base = "work/final_candidate_provenance.parquet"
    with Guard(c) as guard:
        m = evaluate_policy(c, "current_joint_100", art_override=base)
        duplicates = c.sql(f"""SELECT count(*)-count(DISTINCT
            (source1_entity_id,target_entity_id)) FROM read_parquet('{base}')""").fetchone()[0]
        if duplicates or m["gained"] or m["lost"]:
            raise AssertionError("Canonical artifact identity or direct join failed")
        m.update(policy="current_joint_100", duplicates=duplicates,
                 wall_seconds=round(time.time()-t0,2),artifact_bytes=Path(base).stat().st_size,
                 peak_temp_bytes=guard.peak_temp,resource_scope="evaluation of existing artifact")
        (OUT / "current_joint_100.json").write_text(json.dumps(m,indent=2), encoding="utf-8")
        print(json.dumps(m,indent=2), flush=True)
    c.close()


def rank_paths(field: str) -> str:
    files = sorted(OUT.glob(f"rank_{field}_*.parquet"))
    expected = 2 if field == "name" else 8
    if len(files) != expected:
        raise RuntimeError(f"Expected {expected} {field} partitions; found {len(files)}")
    return ",".join("'" + f.as_posix() + "'" for f in files)


EVIDENCE_TUPLE = (
    "both_pass DESC, postal_shared DESC, numeric_shared DESC, "
    "exact_name DESC, exact_addr DESC, round(score,12) DESC, sh DESC, "
    "coverage_s1 DESC, coverage_target DESC, jaccard DESC, mid ASC"
)

DYNAMIC_CASE = ("CASE WHEN coalesce(b.n,0)>0 THEN 25 "
                "WHEN coalesce(a.n,0)>0 OR coalesce(n.n,0) BETWEEN 1 AND 5 THEN 50 "
                "ELSE 75 END")
HEAVY_ORDER = ("exact_addr DESC,postal_shared DESC,numeric_shared DESC,"
               "exact_name DESC,addr_jaccard DESC,mid ASC")


def prepare_evidence_features() -> None:
    c = conn()
    t0 = time.time()
    with Guard(c):
        feature_cols = """entity_id,country_norm cc,
            len(list_distinct(str_split(name_nosuffix,' ')))::SMALLINT name_count,
            len(list_distinct(str_split(addr_norm,' ')))::SMALLINT addr_count,
            regexp_extract(addr_norm,'[0-9]{5,6}') postal,
            regexp_extract(addr_norm,'[0-9]+') num_token"""
        if not (OUT / "evidence_s1.parquet").exists():
            copy(c, f"""SELECT {feature_cols} FROM read_parquet('work/keys/train_s1.parquet') a
                JOIN read_parquet('work/split_s1.parquet') v USING(entity_id)
                WHERE v.split='val'""", "evidence_s1")
        countries = [x[0] for x in c.sql(f"""SELECT DISTINCT cc FROM
            read_parquet('{(OUT / 'evidence_s1.parquet').as_posix()}') ORDER BY 1""").fetchall()]
        for country in countries:
            name = f"evidence_tgt_{country}"
            if (OUT / f"{name}.parquet").exists():
                continue
            both = " UNION ALL ".join(
                f"SELECT {feature_cols} FROM read_parquet('work/keys/train_{src}.parquet') "
                f"WHERE country_norm='{country}'" for src in ("s2", "s3"))
            copy(c, both, name)
        if not (OUT / "exact_name.parquet").exists():
            c.execute("""CREATE TEMP TABLE s1name AS SELECT a.entity_id s1,a.country_norm cc,
                a.name_nosuffix sns FROM read_parquet('work/keys/train_s1.parquet') a
                JOIN read_parquet('work/split_s1.parquet') v ON a.entity_id=v.entity_id
                WHERE v.split='val' AND length(a.name_nosuffix)>0""")
            target = " UNION ALL ".join(source_sql(src,
                "entity_id,country_norm cc,name_nosuffix tns") for src in ("s2", "s3"))
            copy(c, f"""SELECT s.s1,t.entity_id mid FROM s1name s JOIN ({target}) t
                ON s.cc=t.cc AND s.sns=t.tns""", "exact_name")
    c.close()
    print(f"prepare_evidence_features wall_seconds={time.time()-t0:.1f}", flush=True)


def evidence_rank(field: str, country: str, shard: int, repro: bool = False) -> None:
    """Label-free evidence tuple, fixed before validation-label evaluation.

    Input is the top-200/source IDF shortlist; deep GT audit rows are excluded.
    The tuple above orders: agreement across passes, equal first postal-like
    address token, equal first numeric address token, exact normalized name,
    exact normalized address, shared-token inverse DF sum, shared token count,
    S1/target coverage, token Jaccard, then lexical target ID. Source is absent
    from identity evidence and enters only through optional quota selection.
    """
    if field not in ("name", "addr") or shard not in range(32):
        raise ValueError("Expected name/addr field and shard 0..31")
    c = conn()
    t0 = time.time()
    with Guard(c):
        original_name = f"rank_evidence_{field}_{country}_{shard}"
        output_name = f"repro_evidence_{field}_{country}_{shard}" if repro else original_name
        if (OUT / f"{output_name}.parquet").exists():
            print(f"Reusing {output_name}", flush=True)
            return
        this = (OUT / (f"rank_{field}_{country}.parquet" if field == "name"
                      else f"rank_{field}_{country}_{shard%4}.parquet")).as_posix()
        other_field = "addr" if field == "name" else "name"
        other = ",".join("'" + p.as_posix() + "'" for p in sorted(OUT.glob(f"rank_{other_field}_{country}*.parquet")))
        if not other:
            raise RuntimeError("Opposite pass rank artifacts required")
        c.execute(f"""CREATE TEMP TABLE short AS SELECT * FROM read_parquet('{this}')
            WHERE rsource<=200 AND hash(s1)%32={shard}""")
        print(f"evidence {field} {country} shard={shard} shortlist={c.sql('SELECT count(*) FROM short').fetchone()[0]:,}", flush=True)
        c.execute(f"""CREATE TEMP TABLE other AS SELECT s1,mid FROM read_parquet([{other}])
            WHERE rsource<=200 AND hash(s1)%32={shard}""")
        c.execute(f"""CREATE TEMP TABLE exact_n AS SELECT e.s1,e.mid FROM
            read_parquet('{(OUT / 'exact_name.parquet').as_posix()}') e
            WHERE hash(e.s1)%32={shard}""")
        c.execute(f"""CREATE TEMP TABLE exact_a AS SELECT e.s1,e.mid FROM
            read_parquet('{(OUT / 'exact_addr.parquet').as_posix()}') e
            WHERE hash(e.s1)%32={shard}""")
        sc, tc = ("name_count", "name_count") if field == "name" else ("addr_count", "addr_count")
        c.execute(f"""CREATE TEMP TABLE evidence AS SELECT r.s1,r.mid,r.sh,r.score,
            (o.mid IS NOT NULL) both_pass,
            (length(s.postal)>0 AND s.postal=t.postal) postal_shared,
            (length(s.num_token)>0 AND s.num_token=t.num_token) numeric_shared,
            (n.mid IS NOT NULL) exact_name,
            (a.mid IS NOT NULL) exact_addr,
            r.sh::DOUBLE/nullif(s.{sc},0) coverage_s1,
            r.sh::DOUBLE/nullif(t.{tc},0) coverage_target,
            r.sh::DOUBLE/nullif(s.{sc}+t.{tc}-r.sh,0) jaccard
            FROM short r JOIN read_parquet('{(OUT / 'evidence_s1.parquet').as_posix()}') s
              ON r.s1=s.entity_id
            JOIN read_parquet('{(OUT / f'evidence_tgt_{country}.parquet').as_posix()}') t
              ON r.mid=t.entity_id
            LEFT JOIN other o ON r.s1=o.s1 AND r.mid=o.mid
            LEFT JOIN exact_n n ON r.s1=n.s1 AND r.mid=n.mid
            LEFT JOIN exact_a a ON r.s1=a.s1 AND r.mid=a.mid""")
        copy(c, f"""SELECT s1,mid,sh,score,both_pass,postal_shared,numeric_shared,
            exact_name,exact_addr,coverage_s1,coverage_target,jaccard,
            row_number() OVER (PARTITION BY s1 ORDER BY {EVIDENCE_TUPLE})::INT rjoint,
            row_number() OVER (PARTITION BY s1,substr(mid,1,2)
                               ORDER BY {EVIDENCE_TUPLE})::INT rsource
            FROM evidence""", output_name)
        if repro:
            original = (OUT / f"{original_name}.parquet").as_posix()
            rebuilt = (OUT / f"{output_name}.parquet").as_posix()
            changed = c.sql(f"""SELECT count(*) FROM (
                SELECT s1,mid,rjoint,rsource FROM read_parquet('{original}')
                EXCEPT SELECT s1,mid,rjoint,rsource FROM read_parquet('{rebuilt}'))""").fetchone()[0]
            changed += c.sql(f"""SELECT count(*) FROM (
                SELECT s1,mid,rjoint,rsource FROM read_parquet('{rebuilt}')
                EXCEPT SELECT s1,mid,rjoint,rsource FROM read_parquet('{original}'))""").fetchone()[0]
            print(f"restart_repro_rank_differences={changed}", flush=True)
            if changed:
                raise AssertionError("Evidence ranking changed after restart")
    c.close()
    print(f"evidence_rank wall_seconds={time.time()-t0:.1f}", flush=True)


def dynamic(use_evidence: bool = False) -> None:
    """Fixed inference rule: 25/50/75 slots per source, per token pass.

    Strong: any same target matched by sorted name and exact address.
    Medium: any exact address hit, or 1..5 sorted-name candidates.
    Ambiguous: everything else. Hard bound is 75 per source/pass.
    """
    c = conn()
    t0 = time.time()
    output = "evidence_dynamic" if use_evidence else "dynamic"
    def paths(field: str) -> str:
        if not use_evidence:
            return rank_paths(field)
        files = sorted(OUT.glob(f"rank_evidence_{field}_*.parquet"))
        if len(files) != 64:
            raise RuntimeError(f"Expected 64 evidence {field} shards; found {len(files)}")
        return ",".join("'" + f.as_posix() + "'" for f in files)
    with Guard(c) as guard:
        sorted_path = (OUT / "sorted.parquet").as_posix()
        addr_path = (OUT / "exact_addr.parquet").as_posix()
        c.execute(f"""CREATE TEMP TABLE band AS SELECT s.entity_id s1,
            {DYNAMIC_CASE} k_source
            FROM read_parquet('work/keys/train_s1.parquet') s
            JOIN read_parquet('work/split_s1.parquet') v
              ON s.entity_id=v.entity_id AND v.split='val'
            LEFT JOIN (SELECT s1,count(*) n FROM read_parquet('{sorted_path}') GROUP BY 1) n
              ON s.entity_id=n.s1
            LEFT JOIN (SELECT s1,count(*) n FROM read_parquet('{addr_path}') GROUP BY 1) a
              ON s.entity_id=a.s1
            LEFT JOIN (SELECT n.s1,count(*) n FROM read_parquet('{sorted_path}') n
                       JOIN read_parquet('{addr_path}') a USING(s1,mid) GROUP BY 1) b
              ON s.entity_id=b.s1""")
        copy(c, "SELECT * FROM band", f"{output}_bands")
        parts = []
        for shard in range(16):
            pieces = [
                f"SELECT s1,mid,1 b FROM read_parquet('{sorted_path}') WHERE hash(s1)%16={shard}",
                f"SELECT s1,mid,2 b FROM read_parquet('{addr_path}') WHERE hash(s1)%16={shard}",
            ]
            for field, bit in (("name", 4), ("addr", 8)):
                pieces.append(f"""SELECT r.s1,r.mid,{bit} b FROM
                    read_parquet([{paths(field)}]) r JOIN band ON r.s1=band.s1
                    WHERE r.rsource<=band.k_source AND hash(r.s1)%16={shard}""")
            part_name = f"{output}_part{shard}"
            if not (OUT / f"{part_name}.parquet").exists():
                copy(c, f"""SELECT s1 source1_entity_id,mid target_entity_id,
                    bit_or(b)::UTINYINT prov FROM ({' UNION ALL '.join(pieces)}) GROUP BY 1,2""", part_name)
            else:
                print(f"Reusing {part_name}", flush=True)
            parts.append(OUT / f"{part_name}.parquet")
        part_paths = ",".join("'" + x.as_posix() + "'" for x in parts)
        copy(c, f"SELECT * FROM read_parquet([{part_paths}])", output)
        m = evaluate_policy(c, output)
        gt = (OUT / "gt.parquet").as_posix()
        art = (OUT / f"{output}.parquet").as_posix()
        m["bands"] = [dict(zip(("k_per_source", "s1", "candidates", "gt_total", "recovered"), row))
                      for row in c.sql(f"""SELECT b.k_source,count(DISTINCT b.s1),
                        sum(coalesce(ac.n,0)),count(g.mid),count(h.mid)
                        FROM band b
                        LEFT JOIN (SELECT source1_entity_id s1,count(*) n
                                   FROM read_parquet('{art}') GROUP BY 1) ac USING(s1)
                        LEFT JOIN read_parquet('{gt}') g USING(s1)
                        LEFT JOIN hits h ON g.s1=h.s1 AND g.mid=h.mid
                        GROUP BY 1 ORDER BY 1""").fetchall()]
        # Candidate sums above are duplicated by GT multiplicity; recompute them
        # from the one-row-per-S1 band table.
        counts = dict(c.sql(f"""SELECT b.k_source,sum(coalesce(a.n,0))
            FROM band b LEFT JOIN (SELECT source1_entity_id s1,count(*) n
            FROM read_parquet('{art}') GROUP BY 1) a USING(s1) GROUP BY 1""").fetchall())
        for row in m["bands"]:
            row["candidates"] = counts[row["k_per_source"]]
            row["recall"] = row["recovered"] / row["gt_total"] if row["gt_total"] else None
        m.update(policy=output, ranker="evidence_ranker_v1" if use_evidence else "idf",
                 hard_upper_per_source_per_pass=75,
                 wall_seconds=round(time.time()-t0,2),
                 artifact_bytes=(OUT / f"{output}.parquet").stat().st_size,
                 peak_temp_bytes=guard.peak_temp)
        (OUT / f"{output}.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
        print(json.dumps(m, indent=2), flush=True)
    c.close()


def dynamic_detail(use_evidence: bool = False) -> None:
    c = conn()
    prefix="evidence_" if use_evidence else ""
    def paths(field):
        if not use_evidence: return rank_paths(field)
        files=sorted(OUT.glob(f"rank_evidence_{field}_*.parquet"))
        return ",".join("'"+f.as_posix()+"'" for f in files)
    with Guard(c):
        gt = (OUT / "gt.parquet").as_posix()
        dynamic_art = (OUT / f"{prefix}dynamic.parquet").as_posix()
        base = (OUT/"evidence_source_75_75.parquet").as_posix() if use_evidence else "work/final_candidate_provenance.parquet"
        band = (OUT / f"{prefix}dynamic_bands.parquet").as_posix()
        c.execute(f"""CREATE TEMP TABLE lost AS SELECT g.s1,g.mid
            FROM read_parquet('{gt}') g
            JOIN read_parquet('{base}') p ON g.s1=p.source1_entity_id AND g.mid=p.target_entity_id
            ANTI JOIN read_parquet('{dynamic_art}') d
              ON g.s1=d.source1_entity_id AND g.mid=d.target_entity_id""")
        copy(c, f"""SELECT g.s1,g.mid,b.k_source,n.rsource name_source_rank,
            a.rsource addr_source_rank FROM lost g
            JOIN read_parquet('{band}') b ON g.s1=b.s1
            LEFT JOIN (SELECT r.s1,r.mid,r.rsource FROM read_parquet([{paths('name')}]) r
                       JOIN lost l ON r.s1=l.s1 AND r.mid=l.mid) n
              ON g.s1=n.s1 AND g.mid=n.mid
            LEFT JOIN (SELECT r.s1,r.mid,r.rsource FROM read_parquet([{paths('addr')}]) r
                       JOIN lost l ON r.s1=l.s1 AND r.mid=l.mid) a
              ON g.s1=a.s1 AND g.mid=a.mid""", f"{prefix}dynamic_lost_gt")
        f = (OUT / f"{prefix}dynamic_lost_gt.parquet").as_posix()
        row = c.sql(f"""SELECT count(*),count(*) FILTER(WHERE
            coalesce(name_source_rank,2147483647)>k_source AND
            coalesce(addr_source_rank,2147483647)>k_source)
            FROM read_parquet('{f}')""").fetchone()
        if row[0] != row[1]:
            raise AssertionError(f"Only {row[1]}/{row[0]} dynamic losses explained by capping")
        print(f"{prefix}dynamic_lost_gt={row[0]} all_exceed_band={row[1]}", flush=True)
    c.close()


def heavy(cap_filter: int | None = None) -> None:
    """Measure heavy sorted-name caps 50 and 100; retain address pass uncapped."""
    c = conn()
    t0 = time.time()
    with Guard(c) as guard:
        sorted_path = (OUT / "sorted.parquet").as_posix()
        base = "work/final_candidate_provenance.parquet"
        c.execute(f"""CREATE TEMP TABLE heavy_s1 AS SELECT s1,count(*) n
            FROM read_parquet('{sorted_path}') GROUP BY 1 HAVING count(*)>=120""")
        c.execute(f"""CREATE TEMP TABLE heavy_pairs AS SELECT p.s1,p.mid
            FROM read_parquet('{sorted_path}') p JOIN heavy_s1 h USING(s1)""")
        n_heavy = c.sql("SELECT count(*) FROM heavy_pairs").fetchone()[0]
        base_n = c.sql(f"SELECT count(*) FROM read_parquet('{base}')").fetchone()[0]
        gt = (OUT / "gt.parquet").as_posix()
        gt_heavy = c.sql(f"""SELECT count(*) FROM heavy_pairs h JOIN
            read_parquet('{gt}') g USING(s1,mid)""").fetchone()[0]
        c.execute("""CREATE TEMP TABLE s1v AS SELECT a.entity_id,a.name_nosuffix sns,a.addr_norm sad
            FROM read_parquet('work/keys/train_s1.parquet') a
            JOIN read_parquet('work/split_s1.parquet') s
              ON a.entity_id=s.entity_id AND s.split='val'""")
        target = " UNION ALL ".join(
            f"SELECT entity_id,name_nosuffix tns,addr_norm tad FROM "
            f"read_parquet('work/keys/train_{src}.parquet') "
            "WHERE entity_id IN (SELECT mid FROM heavy_pairs)" for src in ("s2", "s3"))
        if not (OUT / "heavy_rank.parquet").exists():
            copy(c, f"""SELECT s1,mid,row_number() OVER (PARTITION BY s1 ORDER BY
            {HEAVY_ORDER})::INT rk FROM (
            SELECT h.s1,h.mid,
                (length(s.sad)>0 AND s.sad=t.tad) exact_addr,
                (length(regexp_extract(s.sad,'[0-9]{{5,6}}'))>0 AND
                 regexp_extract(s.sad,'[0-9]{{5,6}}')=regexp_extract(t.tad,'[0-9]{{5,6}}')) postal_shared,
                (length(regexp_extract(s.sad,'[0-9]+'))>0 AND
                 regexp_extract(s.sad,'[0-9]+')=regexp_extract(t.tad,'[0-9]+')) numeric_shared,
                (length(s.sns)>0 AND s.sns=t.tns) exact_name,
                len(list_intersect(str_split(s.sad,' '),str_split(t.tad,' ')))::DOUBLE /
                  nullif(len(list_distinct(list_concat(str_split(s.sad,' '),str_split(t.tad,' ')))),0) addr_jaccard
            FROM heavy_pairs h JOIN s1v s ON h.s1=s.entity_id
            JOIN ({target}) t ON h.mid=t.entity_id)""", "heavy_rank")
        rank_path = (OUT / "heavy_rank.parquet").as_posix()
        result = {"reference_heavy_candidates": n_heavy, "reference_heavy_gt": gt_heavy}
        for cap in (50, 100):
            if cap_filter is not None and cap != cap_filter:
                continue
            name = f"heavy_cap_{cap}"
            copy(c, f"""SELECT a.source1_entity_id,a.target_entity_id,a.prov
                FROM read_parquet('{base}') a
                LEFT JOIN read_parquet('{rank_path}') h
                  ON a.source1_entity_id=h.s1 AND a.target_entity_id=h.mid
                WHERE h.rk IS NULL OR NOT (a.prov=1 AND h.rk>{cap})""", name)
            result[name] = evaluate_policy(c, name)
            result[name].update(policy=name, artifact_bytes=(OUT / f"{name}.parquet").stat().st_size)
            if base_n - result[name]["candidates"] > n_heavy or result[name]["lost"] > gt_heavy:
                raise AssertionError("Heavy cap removed more than the measured heavy-block reference")
            c.execute("DROP TABLE hits")
            c.execute("DROP TABLE base_hits")
            c.execute("DROP TABLE per_s1")
        result.update(wall_seconds=round(time.time()-t0,2), peak_temp_bytes=guard.peak_temp)
        result_file = f"heavy_cap_{cap_filter}.json" if cap_filter is not None else "heavy.json"
        (OUT / result_file).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2), flush=True)
    c.close()


def combine_heavy(base_name: str, cap: int) -> None:
    base_path = OUT / f"{base_name}.parquet"
    rank_path = OUT / "heavy_rank.parquet"
    if not base_path.exists() or not rank_path.exists():
        raise FileNotFoundError("Base policy and heavy_rank artifacts are required")
    name = f"{base_name}_heavy{cap}"
    c = conn()
    t0 = time.time()
    with Guard(c) as guard:
        copy(c, f"""SELECT a.source1_entity_id,a.target_entity_id,a.prov
            FROM read_parquet('{base_path.as_posix()}') a
            LEFT JOIN read_parquet('{rank_path.as_posix()}') h
              ON a.source1_entity_id=h.s1 AND a.target_entity_id=h.mid
            WHERE h.rk IS NULL OR NOT (a.prov=1 AND h.rk>{cap})""", name)
        m = evaluate_policy(c, name)
        m.update(policy=name,base_policy=base_name,sorted_heavy_cap=cap,
                 exact_address_uncapped=True,wall_seconds=round(time.time()-t0,2),
                 artifact_bytes=(OUT/f"{name}.parquet").stat().st_size,
                 peak_temp_bytes=guard.peak_temp)
        if m["candidates"] > c.sql(f"SELECT count(*) FROM read_parquet('{base_path.as_posix()}')").fetchone()[0]:
            raise AssertionError("Heavy refinement increased candidate count")
        (OUT/f"{name}.json").write_text(json.dumps(m,indent=2),encoding="utf-8")
        print(json.dumps(m,indent=2),flush=True)
    c.close()


def sister() -> None:
    """Bounded label-free other-source expansion from exact-address/token seeds."""
    c = conn()
    t0 = time.time()
    with Guard(c) as guard:
        base = "work/final_candidate_provenance.parquet"
        target = " UNION ALL ".join(source_sql(src,
            "entity_id,country_norm cc,name_sorted ssrt,substr(entity_id,1,2) src")
            for src in ("s2", "s3"))
        c.execute(f"""CREATE TEMP TABLE seed_ids AS SELECT s1,mid FROM (
            SELECT source1_entity_id s1,target_entity_id mid,
                   row_number() OVER (PARTITION BY source1_entity_id
                                      ORDER BY target_entity_id) seed_rank
            FROM read_parquet('{base}')
            WHERE (prov&2)>0 AND (prov&12)>0 AND (prov&1)=0)
            WHERE seed_rank<=3""")
        c.execute(f"""CREATE TEMP TABLE seed AS SELECT s.s1,s.mid,t.cc,t.ssrt,t.src
            FROM seed_ids s JOIN ({target}) t ON s.mid=t.entity_id
            WHERE length(t.ssrt)>0""")
        seed_n = c.sql("SELECT count(*) FROM seed").fetchone()[0]
        c.execute("""CREATE TEMP TABLE sister_keys AS SELECT DISTINCT cc,ssrt,
            CASE WHEN src='S2' THEN 'S3' ELSE 'S2' END target_src FROM seed""")
        c.execute(f"""CREATE TEMP TABLE sister_catalog AS SELECT t.entity_id mid,t.cc,t.ssrt,t.src
            FROM ({target}) t JOIN sister_keys k
            ON t.cc=k.cc AND t.ssrt=k.ssrt AND t.src=k.target_src""")
        raw_n = c.sql("SELECT count(*) FROM sister_catalog").fetchone()[0]
        c.execute("""CREATE TEMP TABLE valid_sister_keys AS SELECT cc,ssrt,src
            FROM sister_catalog GROUP BY 1,2,3 HAVING count(*)<=20""")
        c.execute("""CREATE TEMP TABLE sister_raw AS SELECT s.s1,s.mid seed_mid,t.mid
            FROM seed s JOIN valid_sister_keys k
            ON s.cc=k.cc AND s.ssrt=k.ssrt AND s.src<>k.src
            JOIN sister_catalog t ON t.cc=k.cc AND t.ssrt=k.ssrt AND t.src=k.src""")
        copy(c, f"""SELECT s1,mid FROM (
            SELECT s1,mid,row_number() OVER (PARTITION BY s1 ORDER BY mid) rk FROM
                (SELECT DISTINCT s1,mid FROM sister_raw)
            ) WHERE rk<=20
            EXCEPT SELECT source1_entity_id,target_entity_id FROM read_parquet('{base}')""", "sister_added")
        added = (OUT / "sister_added.parquet").as_posix()
        copy(c, f"""SELECT source1_entity_id,target_entity_id,prov FROM read_parquet('{base}')
            UNION ALL SELECT s1,mid,16::UTINYINT FROM read_parquet('{added}')""", "sister_expanded")
        m = evaluate_policy(c, "sister_expanded")
        gt = (OUT / "gt.parquet").as_posix()
        src = c.sql(f"""SELECT substr(a.mid,1,2),count(*),count(g.mid) FROM
            read_parquet('{added}') a LEFT JOIN read_parquet('{gt}') g USING(s1,mid)
            GROUP BY 1 ORDER BY 1""").fetchall()
        m["added_by_source"] = {s: dict(candidates=n,gt_links=h) for s,n,h in src}
        m["new_gt_per_1000_added"] = m["gained"]*1000 / c.sql(f"SELECT count(*) FROM read_parquet('{added}')").fetchone()[0] if src else 0
        m["seeds"] = seed_n
        m["eligible_target_catalog_rows_before_freq_guard"] = raw_n
        m["added_per_s1"] = dict(zip(("mean","p95","p99","max"), c.sql(f"""SELECT avg(n),quantile_cont(n,.95),
            quantile_cont(n,.99),max(n) FROM (SELECT s1,count(*) n FROM
            read_parquet('{added}') GROUP BY 1)""").fetchone()))
        m.update(policy="sister_expanded", wall_seconds=round(time.time()-t0,2),
                 artifact_bytes=(OUT / "sister_expanded.parquet").stat().st_size,
                 peak_temp_bytes=guard.peak_temp)
        (OUT / "sister_expanded.json").write_text(json.dumps(m,indent=2), encoding="utf-8")
        print(json.dumps(m,indent=2), flush=True)
    c.close()


def diagnostics() -> None:
    c = conn()
    with Guard(c):
        diagnostics_inner(c)
    c.close()


def diagnostics_inner(c) -> None:
    rank_files = {field: sorted(OUT.glob(f"rank_{field}_*.parquet")) for field in ("name", "addr")}
    if any(len(files) < 2 for files in rank_files.values()):
        raise RuntimeError("Token ranks are required for pass-rank diagnostics")
    for field, files in rank_files.items():
        paths = ",".join("'" + f.as_posix() + "'" for f in files)
        c.execute(f"""CREATE TEMP TABLE rank_{field} AS SELECT s1,mid,rjoint,rsource
            FROM read_parquet([{paths}]) WHERE (s1,mid) IN
            (SELECT s1,mid FROM read_parquet('{(OUT / 'cap_ranking_loss.parquet').as_posix()}'))""")
    result = {}
    for pop in ("key_ineligible_loss", "cap_ranking_loss"):
        f = (OUT / f"{pop}.parquet").as_posix()
        n = c.sql(f"SELECT count(*) FROM read_parquet('{f}')").fetchone()[0]
        groups = {}
        for label, expr in {
            "source": "substr(mid,1,2)", "country": "cc",
            "address_missingness": "CASE WHEN length(sad)=0 AND length(tad)=0 THEN 'both_missing' WHEN length(sad)=0 THEN 's1_missing' WHEN length(tad)=0 THEN 'target_missing' ELSE 'both_present' END",
            "eligible_passes": "concat(CASE WHEN sorted_eligible THEN 'sorted ' ELSE '' END, CASE WHEN addr_eligible THEN 'exact_addr ' ELSE '' END, CASE WHEN eligible_name_overlap>0 THEN 'name_token ' ELSE '' END, CASE WHEN eligible_addr_overlap>0 THEN 'addr_token' ELSE '' END)",
        }.items():
            groups[label] = dict(c.sql(f"SELECT {expr},count(*) FROM read_parquet('{f}') GROUP BY 1 ORDER BY 1").fetchall())
        overlap = {}
        for col in ("raw_name_overlap", "raw_addr_overlap", "eligible_name_overlap", "eligible_addr_overlap"):
            overlap[col] = dict(c.sql(f"""SELECT CASE WHEN {col}=0 THEN '0' WHEN {col}=1 THEN '1'
                WHEN {col}=2 THEN '2' ELSE '3+' END,count(*)
                FROM read_parquet('{f}') GROUP BY 1 ORDER BY 1""").fetchall())
        rank_stats = {}
        if pop == "cap_ranking_loss":
            for field in ("name", "addr"):
                flag = f"eligible_{field}_overlap>0"
                row = c.sql(f"""SELECT count(*) FILTER(WHERE {flag}),
                    count(*) FILTER(WHERE {flag} AND r.rjoint IS NULL),
                    min(r.rjoint) FILTER(WHERE {flag}),
                    median(r.rjoint) FILTER(WHERE {flag}),
                    quantile_cont(r.rjoint,.95) FILTER(WHERE {flag}),
                    max(r.rjoint) FILTER(WHERE {flag})
                    FROM read_parquet('{f}') p LEFT JOIN rank_{field} r USING(s1,mid)""").fetchone()
                rank_stats[field] = dict(zip(("eligible", "missing_rank", "min", "median", "p95", "max"), row))
                rank_stats[field]["joint_bands"] = dict(c.sql(f"""SELECT CASE
                    WHEN rjoint<=100 THEN '001-100' WHEN rjoint<=150 THEN '101-150'
                    WHEN rjoint<=200 THEN '151-200' ELSE '201+' END,count(*)
                    FROM read_parquet('{f}') p JOIN rank_{field} r USING(s1,mid)
                    WHERE {flag} GROUP BY 1 ORDER BY 1""").fetchall())
                rank_stats[field]["source_bands"] = dict(c.sql(f"""SELECT CASE
                    WHEN rsource<=50 THEN '001-050' WHEN rsource<=75 THEN '051-075'
                    WHEN rsource<=100 THEN '076-100' WHEN rsource<=200 THEN '101-200'
                    ELSE '201+' END,count(*)
                    FROM read_parquet('{f}') p JOIN rank_{field} r USING(s1,mid)
                    WHERE {flag} GROUP BY 1 ORDER BY 1""").fetchall())
        result[pop] = dict(count=n, groups=groups, overlap=overlap, ranks=rank_stats)
    result["total_selected_loss"] = sum(result[p]["count"] for p in ("key_ineligible_loss", "cap_ranking_loss"))
    (OUT / "audit_diagnostics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)


def discrepancy() -> None:
    c = conn()
    with Guard(c):
        old = "work/final_candidate_provenance.parquet"
        new = (OUT / "joint_100.parquet").as_posix()
        if not (OUT / "candidate_delta.parquet").exists():
            copy(c, f"""SELECT o.source1_entity_id s1,o.target_entity_id mid,'canonical_only' side,o.prov
                FROM read_parquet('{old}') o ANTI JOIN read_parquet('{new}') n
                ON o.source1_entity_id=n.source1_entity_id AND o.target_entity_id=n.target_entity_id
                UNION ALL
                SELECT n.source1_entity_id,n.target_entity_id,'rebuild_only',n.prov
                FROM read_parquet('{new}') n ANTI JOIN read_parquet('{old}') o
                ON o.source1_entity_id=n.source1_entity_id AND o.target_entity_id=n.target_entity_id""", "candidate_delta")
        d = (OUT / "candidate_delta.parquet").as_posix()
        output = {"side_by_prov": [dict(zip(("side","prov","n"), r)) for r in
                   c.sql(f"SELECT side,prov,count(*) FROM read_parquet('{d}') GROUP BY 1,2 ORDER BY 1,2").fetchall()]}
        for field in ("name", "addr"):
            c.execute("DROP TABLE IF EXISTS r")
            c.execute("DROP TABLE IF EXISTS b")
            c.execute(f"""CREATE TEMP TABLE r AS SELECT p.s1,p.mid,p.rjoint,p.score
                FROM read_parquet([{rank_paths(field)}]) p
                JOIN read_parquet('{d}') d USING(s1,mid)""")
            c.execute(f"""CREATE TEMP TABLE b AS SELECT p.s1,p.score score100
                FROM read_parquet([{rank_paths(field)}]) p
                WHERE p.rjoint=100 AND p.s1 IN
                  (SELECT s1 FROM read_parquet('{d}'))""")
            output[field] = [dict(zip(("side", "rank_band", "n", "min_rank", "max_rank", "same_score_as_100"), row))
                for row in c.sql(f"""SELECT d.side,CASE WHEN r.rjoint<=100 THEN '<=100'
                            WHEN r.rjoint<=110 THEN '101-110'
                            WHEN r.rjoint<=200 THEN '111-200'
                            WHEN r.rjoint IS NULL THEN 'not_shortlisted' ELSE '>200' END,
                           count(*),min(r.rjoint),max(r.rjoint),
                           count(*) FILTER(WHERE r.score=b.score100)
                    FROM read_parquet('{d}') d
                    LEFT JOIN r USING(s1,mid)
                    LEFT JOIN b ON d.s1=b.s1 GROUP BY 1,2 ORDER BY 1,2""").fetchall()]
        (OUT / "candidate_discrepancy.json").write_text(json.dumps(output,indent=2), encoding="utf-8")
        print(json.dumps(output,indent=2), flush=True)
    c.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["audit", "baseline", "rank", "policy", "metrics", "compare", "diagnostics", "dynamic", "evidence_dynamic", "dynamic_detail", "evidence_dynamic_detail", "evidence_rank", "prepare_evidence_features", "heavy", "combine_heavy", "discrepancy", "sister"])
    ap.add_argument("--name")
    ap.add_argument("--field", choices=["name", "addr"])
    ap.add_argument("--country")
    ap.add_argument("--shard", type=int)
    ap.add_argument("--cap", type=int, choices=[50, 100])
    ap.add_argument("--repro", action="store_true")
    ap.add_argument("--left")
    ap.add_argument("--right")
    args = ap.parse_args()
    if args.stage == "audit":
        audit()
    elif args.stage == "baseline":
        baseline()
    elif args.stage == "rank":
        rank(args.field, args.country, args.shard)
    elif args.stage == "policy":
        if not args.name:
            ap.error("policy requires --name")
        if args.name not in POLICIES:
            ap.error(f"unknown policy {args.name}")
        policy(args.name)
    elif args.stage == "metrics":
        if not args.name:
            ap.error("metrics requires --name")
        metrics(args.name)
    elif args.stage == "compare":
        if not args.left or not args.right:
            ap.error("compare requires --left and --right")
        compare(args.left,args.right)
    elif args.stage == "diagnostics":
        diagnostics()
    elif args.stage == "dynamic":
        dynamic()
    elif args.stage == "evidence_dynamic":
        dynamic(use_evidence=True)
    elif args.stage == "dynamic_detail":
        dynamic_detail()
    elif args.stage == "evidence_dynamic_detail":
        dynamic_detail(use_evidence=True)
    elif args.stage == "evidence_rank":
        if args.field is None or args.country is None or args.shard is None:
            ap.error("evidence_rank requires --field, --country and --shard")
        evidence_rank(args.field, args.country, args.shard, args.repro)
    elif args.stage == "prepare_evidence_features":
        prepare_evidence_features()
    elif args.stage == "heavy":
        heavy(args.cap)
    elif args.stage == "combine_heavy":
        if not args.name or args.cap is None:
            ap.error("combine_heavy requires --name and --cap")
        combine_heavy(args.name,args.cap)
    elif args.stage == "discrepancy":
        discrepancy()
    elif args.stage == "sister":
        sister()


if __name__ == "__main__":
    main()
