"""Deterministic matcher splits with a model-final-eval firewall."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import duckdb

SEED = "matcher_split_v1_20260926"
COUNTS = {"candidate_dev": 220_531, "model_train": 1_765_608,
          "model_calibration": 110_341, "model_final_eval": 110_341}


def _logical_checksum(con, manifest: Path, split: str) -> str:
    h=hashlib.sha256()
    rows=con.sql(f"SELECT entity_id FROM read_parquet('{manifest.as_posix()}') WHERE split=? ORDER BY entity_id",params=[split]).fetchall()
    for (entity_id,) in rows: h.update((entity_id+"\n").encode())
    return h.hexdigest()


def create(output: Path, report: Path, checksums: Path) -> dict:
    con=duckdb.connect(); con.execute("SET memory_limit='800MB'; SET threads=1; SET preserve_insertion_order=false")
    con.execute("SET temp_directory='work/matcher_split_tmp'"); Path("work/matcher_split_tmp").mkdir(exist_ok=True)
    untouched=COUNTS["model_train"]+COUNTS["model_calibration"]+COUNTS["model_final_eval"]
    sql=f"""WITH base AS (
      SELECT entity_id,split old_split FROM read_parquet('work/split_s1.parquet')
    ), ranked AS (
      SELECT entity_id,row_number() OVER(ORDER BY md5(entity_id||':{SEED}'),entity_id) rn
      FROM base WHERE old_split='train'
    )
    SELECT entity_id,CASE
      WHEN old_split='val' THEN 'candidate_dev'
      WHEN rn<={COUNTS['model_train']} THEN 'model_train'
      WHEN rn<={COUNTS['model_train']+COUNTS['model_calibration']} THEN 'model_calibration'
      ELSE 'model_final_eval' END split
    FROM base LEFT JOIN ranked USING(entity_id)"""
    partial=output.with_suffix('.partial.parquet'); partial.unlink(missing_ok=True)
    con.execute(f"COPY ({sql}) TO '{partial.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)"); os.replace(partial,output)
    got=dict(con.sql(f"SELECT split,count(*) FROM read_parquet('{output.as_posix()}') GROUP BY 1").fetchall())
    if got != COUNTS: raise RuntimeError(f"Unexpected split counts: {got}")
    con.execute("""CREATE TEMP TABLE gt AS SELECT source1_entity_id s1,trim(mid) mid
      FROM read_csv('dataset/train/train_ground_truth.tsv',delim='\t',header=true,quote='',all_varchar=true),
      unnest(string_split(matched_entity_ids,',')) u(mid)
      WHERE matched_entity_ids IS NOT NULL AND length(trim(mid))>0""")
    overlaps=con.sql(f"""SELECT count(*) FROM (SELECT g.mid,count(DISTINCT m.split) n
      FROM gt g JOIN read_parquet('{output.as_posix()}') m ON g.s1=m.entity_id GROUP BY 1 HAVING n>1)""").fetchone()[0]
    duplicates=con.sql(f"SELECT count(*)-count(DISTINCT entity_id) FROM read_parquet('{output.as_posix()}')").fetchone()[0]
    summary={"schema_version":1,"seed":SEED,"assignment":"md5(entity_id || seed), entity_id; exact 80/5/5 quotas after preserved candidate_dev","counts":got,"s1_overlap":duplicates,"labelled_target_overlap":overlaps,"splits":{}}
    for split in COUNTS:
        if split == "model_final_eval":
            summary["splits"][split]={"s1":COUNTS[split],
                "entity_id_sha256":_logical_checksum(con,output,split),
                "firewall":"membership and ID checksum only; no label aggregates persisted"}
            continue
        # Aggregates only. No model scores or feature/label artifacts are created for final eval.
        row=con.sql(f"""WITH x AS (SELECT k.entity_id,k.country_norm,m.split
          FROM read_parquet('work/keys/train_s1.parquet') k JOIN read_parquet('{output.as_posix()}') m USING(entity_id)
          WHERE m.split=?), gc AS (SELECT s1,count(*) n FROM gt GROUP BY 1)
          SELECT count(*),sum(coalesce(gc.n,0)),sum(gc.n IS NULL),avg(coalesce(gc.n,0))
          FROM x LEFT JOIN gc ON x.entity_id=gc.s1""",params=[split]).fetchone()
        countries=dict(con.sql(f"""SELECT k.country_norm,count(*) FROM read_parquet('work/keys/train_s1.parquet') k
          JOIN read_parquet('{output.as_posix()}') m USING(entity_id) WHERE m.split=? GROUP BY 1 ORDER BY 1""",params=[split]).fetchall())
        match_dist=dict(con.sql(f"""WITH gc AS (SELECT s1,count(*) n FROM gt GROUP BY 1)
          SELECT coalesce(gc.n,0),count(*) FROM read_parquet('{output.as_posix()}') m
          LEFT JOIN gc ON m.entity_id=gc.s1 WHERE m.split=? GROUP BY 1 ORDER BY 1""",params=[split]).fetchall())
        summary["splits"][split]={"s1":row[0],"gt_links":row[1],"singletons":row[2],"mean_links":row[3],"country":countries,"match_count_distribution":match_dist,"entity_id_sha256":_logical_checksum(con,output,split)}
    checksums.write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    lines=["# Matcher split v1","",f"Seed: `{SEED}`. Candidate development remains unchanged; the untouched 90% is partitioned exactly by deterministic MD5 order.","", "| Split | S1 | GT links | Singletons | ID SHA-256 |","|---|---:|---:|---:|---|"]
    for s,d in summary["splits"].items(): lines.append(f"| {s} | {d['s1']:,} | {d.get('gt_links','firewalled')} | {d.get('singletons','firewalled')} | `{d['entity_id_sha256']}` |")
    lines += ["",f"S1 duplicates/overlap: {duplicates}. Labelled target IDs crossing splits: {overlaps}.","", "The persisted `model_final_eval` record contains membership count and ID checksum only. During the initial split audit, GT-link and singleton aggregates were inadvertently computed before the stricter firewall was applied; they were removed and never used for features, training, scoring, thresholds, or model comparison. No features, labels, predictions, or model metrics were materialized for this split.","", "## Aggregate distributions","","```json",json.dumps(summary["splits"],indent=2),"```",""]
    report.write_text("\n".join(lines),encoding="utf-8"); con.close(); return summary


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',type=Path,default=Path('work/matcher_split_manifest.parquet')); ap.add_argument('--report',type=Path,default=Path('work/matcher_split_report.md')); ap.add_argument('--checksums',type=Path,default=Path('work/matcher_split_checksums.json')); a=ap.parse_args(); print(json.dumps(create(a.output,a.report,a.checksums),indent=2))


if __name__=='__main__': main()
