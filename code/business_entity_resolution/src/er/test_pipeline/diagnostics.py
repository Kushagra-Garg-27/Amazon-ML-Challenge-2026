"""Label-free country-level structural diagnostics for frozen test inference."""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .common import save_json


def run() -> list[dict]:
    temp = Path("work/test_diagnostics_tmp")
    temp.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.execute("SET memory_limit='700MB'; SET threads=1")
    c.execute(f"SET temp_directory='{temp.as_posix()}'")
    c.execute("""CREATE TEMP VIEW s1 AS SELECT entity_id,country_norm,addr_norm
       FROM read_parquet('work/keys/test_s1.parquet')""")
    c.execute("""CREATE TEMP VIEW cand AS SELECT source1_entity_id,target_entity_id,provenance
       FROM read_parquet('work/test_candidates/*.parquet')""")
    c.execute("""CREATE TEMP VIEW scored AS SELECT source1_entity_id,target_entity_id,score,
       predicted,target_is_s2,address_missing FROM read_parquet('work/test_scores/*.parquet')""")
    summary = []
    countries = [r[0] for r in c.execute("SELECT DISTINCT country_norm FROM s1 ORDER BY 1").fetchall()]
    for country in countries:
        base = c.execute("""SELECT count(*),count(*) FILTER(WHERE addr_norm='')
            FROM s1 WHERE country_norm=?""", [country]).fetchone()
        counts = c.execute("""WITH n AS (
          SELECT s.entity_id,count(c.target_entity_id) n,
             count(c.target_entity_id) FILTER(WHERE c.target_entity_id LIKE 'S2-%') n_s2,
             count(c.target_entity_id) FILTER(WHERE c.target_entity_id LIKE 'S3-%') n_s3
          FROM s1 s LEFT JOIN cand c ON s.entity_id=c.source1_entity_id
          WHERE s.country_norm=? GROUP BY 1)
          SELECT avg(n),median(n),quantile_cont(n,.95),quantile_cont(n,.99),max(n),
            count(*) FILTER(WHERE n=0),sum(n_s2),sum(n_s3) FROM n""", [country]).fetchone()
        score = c.execute("""SELECT count(*),count(*) FILTER(WHERE predicted),
          count(*) FILTER(WHERE target_is_s2),count(*) FILTER(WHERE NOT target_is_s2),
          count(*) FILTER(WHERE predicted AND target_is_s2),
          count(*) FILTER(WHERE predicted AND NOT target_is_s2),
          count(*) FILTER(WHERE address_missing),
          approx_quantile(score,.01),approx_quantile(score,.5),
          approx_quantile(score,.95),approx_quantile(score,.99),
          count(*) FILTER(WHERE score IS NULL OR NOT isfinite(score))
          FROM scored q JOIN s1 s ON q.source1_entity_id=s.entity_id
          WHERE s.country_norm=?""", [country]).fetchone()
        accepted = c.execute("""WITH n AS (
          SELECT s.entity_id,count(q.target_entity_id) FILTER(WHERE q.predicted) n
          FROM s1 s LEFT JOIN scored q ON s.entity_id=q.source1_entity_id
          WHERE s.country_norm=? GROUP BY 1)
          SELECT avg(n),count(*) FILTER(WHERE n=0) FROM n""", [country]).fetchone()
        provenance = dict(c.execute("""SELECT provenance,count(*) FROM cand p JOIN s1 s
          ON p.source1_entity_id=s.entity_id WHERE s.country_norm=? GROUP BY 1 ORDER BY 1""",
          [country]).fetchall())
        summary.append({"country": country, "s1": base[0], "s1_address_missing": base[1],
            "candidate_mean": counts[0], "candidate_median": counts[1],
            "candidate_p95": counts[2], "candidate_p99": counts[3], "candidate_max": counts[4],
            "zero_candidate_s1": counts[5], "candidate_s2": counts[6], "candidate_s3": counts[7],
            "scored_rows": score[0], "accepted_rows": score[1],
            "scored_s2": score[2], "scored_s3": score[3],
            "accepted_s2": score[4], "accepted_s3": score[5],
            "candidate_address_missing": score[6],
            "score_p01": score[7], "score_median": score[8],
            "score_p95": score[9], "score_p99": score[10],
            "score_nonfinite": score[11], "accepted_mean": accepted[0],
            "empty_prediction_s1": accepted[1],
            "empty_prediction_rate": accepted[1]/base[0],
            "provenance": {str(k): v for k,v in provenance.items()}})
    c.close()
    path = Path("work/test_structural_diagnostics.parquet")
    path.parent.mkdir(exist_ok=True)
    pq.write_table(pa.Table.from_pylist([
        {**{k:v for k,v in row.items() if k != "provenance"},
         "provenance_json": json.dumps(row["provenance"], sort_keys=True)}
        for row in summary]), path, compression="zstd")
    save_json(Path("work/test_structural_diagnostics.json"), {"status": "PASS", "countries": summary})
    lines = ["# Label-free test structural diagnostics", "",
             "These counts and approximate score quantiles use no test labels and do not estimate accuracy.", "",
             "| Country | S1 | Mean candidates | P95 | Zero candidates | Accepted/S1 | Empty prediction rate |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for row in summary:
        lines.append(f"| {row['country']} | {row['s1']:,} | {row['candidate_mean']:.2f} | {row['candidate_p95']:.1f} | {row['zero_candidate_s1']:,} | {row['accepted_mean']:.3f} | {row['empty_prediction_rate']:.3%} |")
    lines += ["", "France is processed with the same frozen policy. Distribution differences are diagnostic only.", ""]
    Path("work/test_structural_diagnostics.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), indent=2), flush=True)
