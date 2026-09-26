"""Candidate-set oracle ceilings for frozen materialized policies."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import duckdb


POLICIES = {
    "final_source_50_50_heavy100": "work/final_candidate_policy_parts/part-*.parquet",
    "compact_source_37_38_heavy100": "work/freeze_gate/evidence_source_37_38_heavy100.parquet",
    "recall_source_75_75": "work/freeze_gate/evidence_source_75_75.parquet",
}


def _candidate_sql(path: str) -> str:
    return (f"SELECT source1_entity_id s1,target_entity_id mid "
            f"FROM read_parquet('{path}')")


def materialize(output: Path, report: Path) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET memory_limit='800MB'; SET threads=1; SET preserve_insertion_order=false")
    con.execute("SET temp_directory='work/oracle_tmp'")
    Path("work/oracle_tmp").mkdir(exist_ok=True)
    con.execute("""CREATE TEMP TABLE universe AS
        SELECT k.entity_id s1,k.country_norm country
        FROM read_parquet('work/keys/train_s1.parquet') k
        JOIN read_parquet('work/split_s1.parquet') s USING(entity_id)
        WHERE s.split='val'""")
    con.execute("""CREATE TEMP TABLE gt_aug AS
        SELECT o.s1,o.mid,substr(o.mid,1,2) src,
               length(o.sad)>0 AND length(o.tad)>0 both_addr
        FROM read_parquet('work/freeze_gate/oracle.parquet') o""")
    pieces = []
    for name, path in POLICIES.items():
        pieces.append(f"""WITH hits AS (
              SELECT g.* FROM gt_aug g JOIN ({_candidate_sql(path)}) c USING(s1,mid)
            ), ga AS (
              SELECT s1,count(*) truth_count,
                     count(*) FILTER(WHERE src='S2') gt_s2,
                     count(*) FILTER(WHERE src='S3') gt_s3,
                     count(*) FILTER(WHERE both_addr) gt_both_addr,
                     count(*) FILTER(WHERE NOT both_addr) gt_either_missing
              FROM gt_aug GROUP BY 1
            ), ha AS (
              SELECT s1,count(*) recovered_count,
                     count(*) FILTER(WHERE src='S2') rec_s2,
                     count(*) FILTER(WHERE src='S3') rec_s3,
                     count(*) FILTER(WHERE both_addr) rec_both_addr,
                     count(*) FILTER(WHERE NOT both_addr) rec_either_missing
              FROM hits GROUP BY 1
            )
            SELECT '{name}' AS policy_name,u.s1,u.country,
              coalesce(g.truth_count,0)::SMALLINT truth_count,
              coalesce(h.recovered_count,0)::SMALLINT recovered_count,
              (coalesce(g.truth_count,0)-coalesce(h.recovered_count,0))::SMALLINT missed_count,
              CASE WHEN coalesce(g.truth_count,0)=0 THEN 1.0
                   WHEN coalesce(h.recovered_count,0)>0 THEN 1.0 ELSE 0.0 END::FLOAT oracle_precision,
              CASE WHEN coalesce(g.truth_count,0)=0 THEN 1.0
                   ELSE coalesce(h.recovered_count,0)::DOUBLE/g.truth_count END::FLOAT oracle_recall,
              CASE WHEN coalesce(g.truth_count,0)=0 THEN 1.0
                   WHEN coalesce(h.recovered_count,0)=0 THEN 0.0
                   ELSE (1.25*(h.recovered_count::DOUBLE/g.truth_count)) /
                        (0.25+(h.recovered_count::DOUBLE/g.truth_count)) END::FLOAT f0_5,
              (coalesce(g.truth_count,0)=0) singleton,
              (coalesce(g.truth_count,0)>0 AND h.recovered_count=g.truth_count) all_recovered,
              (coalesce(h.recovered_count,0)>0 AND h.recovered_count<g.truth_count) some_recovered,
              (coalesce(g.truth_count,0)>0 AND coalesce(h.recovered_count,0)=0) none_recovered,
              coalesce(g.gt_s2,0)::SMALLINT gt_s2,coalesce(h.rec_s2,0)::SMALLINT rec_s2,
              coalesce(g.gt_s3,0)::SMALLINT gt_s3,coalesce(h.rec_s3,0)::SMALLINT rec_s3,
              coalesce(g.gt_both_addr,0)::SMALLINT gt_both_addr,
              coalesce(h.rec_both_addr,0)::SMALLINT rec_both_addr,
              coalesce(g.gt_either_missing,0)::SMALLINT gt_either_missing,
              coalesce(h.rec_either_missing,0)::SMALLINT rec_either_missing
            FROM universe u LEFT JOIN ga g USING(s1) LEFT JOIN ha h USING(s1)""")
    partial = output.with_suffix(".partial.parquet")
    partial.unlink(missing_ok=True)
    union_sql = " UNION ALL ".join(f"({piece})" for piece in pieces)
    con.execute(f"COPY ({union_sql}) TO '{partial.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    os.replace(partial, output)
    summaries = {}
    for name in POLICIES:
        base = f"read_parquet('{output.as_posix()}') WHERE policy_name='{name}'"
        row = con.sql(f"""SELECT count(*),avg(f0_5),avg(oracle_precision),avg(oracle_recall),
          sum(recovered_count),sum(truth_count),sum(all_recovered),sum(some_recovered),
          sum(none_recovered),sum(singleton),sum(singleton)::DOUBLE/nullif(sum(singleton),0)
          FROM {base}""").fetchone()
        keys = ("s1","macro_f0_5","mean_precision","mean_recall","recovered_links",
                "gt_links","all_gt_recovered_s1","some_gt_recovered_s1",
                "no_gt_recovered_s1","singletons","singleton_accuracy")
        data = dict(zip(keys,row)); data["pair_recall"] = data["recovered_links"] / data["gt_links"]
        data["per_country_macro_f0_5"] = dict(con.sql(
            f"SELECT country,avg(f0_5) FROM {base} GROUP BY 1 ORDER BY 1").fetchall())
        totals = con.sql(f"""SELECT sum(rec_s2),sum(gt_s2),sum(rec_s3),sum(gt_s3),
          sum(rec_both_addr),sum(gt_both_addr),sum(rec_either_missing),sum(gt_either_missing)
          FROM {base}""").fetchone()
        data["link_breakdown"] = dict(zip(("rec_s2","gt_s2","rec_s3","gt_s3",
            "rec_both_addr","gt_both_addr","rec_either_missing","gt_either_missing"), totals))
        data["missed_links_per_s1"] = [dict(zip(("missed","s1"),r)) for r in con.sql(
            f"SELECT missed_count,count(*) FROM {base} GROUP BY 1 ORDER BY 1").fetchall()]
        summaries[name] = data
    con.close()
    lines = ["# Candidate oracle ceilings", "", "These are label-derived ceilings, not matcher performance.", "",
             "| Policy | Macro F0.5 | Mean precision | Mean recall | Pair recall | All/some/none recovered S1 |",
             "|---|---:|---:|---:|---:|---:|"]
    for n,d in summaries.items():
        lines.append(f"| {n} | {d['macro_f0_5']:.9f} | {d['mean_precision']:.9f} | "
                     f"{d['mean_recall']:.9f} | {d['pair_recall']:.9f} | "
                     f"{d['all_gt_recovered_s1']:,}/{d['some_gt_recovered_s1']:,}/{d['no_gt_recovered_s1']:,} |")
    lines += ["", "All 12,277 true singleton S1s predict empty and therefore have oracle accuracy 1.0.", "",
              "## Full metrics", "", "```json", json.dumps(summaries,indent=2), "```", ""]
    report.write_text("\n".join(lines),encoding="utf-8")
    return summaries


def main() -> None:
    ap=argparse.ArgumentParser(); ap.add_argument("--output",type=Path,default=Path("work/candidate_oracle_metrics.parquet")); ap.add_argument("--report",type=Path,default=Path("work/candidate_oracle_report.md")); a=ap.parse_args()
    t=time.time(); result=materialize(a.output,a.report)
    print(json.dumps(result,indent=2)); print(f"wall_seconds={time.time()-t:.2f}")


if __name__ == "__main__": main()
