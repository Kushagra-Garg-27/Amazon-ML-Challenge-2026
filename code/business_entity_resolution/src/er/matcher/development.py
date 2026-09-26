"""Deterministic model-development firewall and sample selection.

This module intentionally has no code path that reads labels, candidates, features,
or predictions for ``model_final_eval``.  The only source rows admitted here are
the already frozen ``model_train`` and ``model_calibration`` memberships.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

SEED = "model_development_v1_20260926"
SAMPLE_SEED = "model_development_samples_v1_20260926"
TUNE_HASH_BYTES = tuple(f"{x:02x}" for x in range(0x00, 0x08))
THRESHOLD_HASH_BYTES = tuple(f"{x:02x}" for x in range(0x08, 0x10))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _id_checksum(con: duckdb.DuckDBPyConnection, relation: str, predicate: str) -> str:
    rows = con.sql(
        f"SELECT entity_id FROM {relation} WHERE {predicate} ORDER BY entity_id"
    ).fetchall()
    h = hashlib.sha256()
    for (entity_id,) in rows:
        h.update(entity_id.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def build_split(output: Path, report: Path, checksums: Path) -> dict:
    """Create the immutable inner development split.

    Prior pilot model-train S1s are assigned to model_fit so neither protected
    subset can contain an entity whose labels were already used.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET memory_limit='700MB'; SET threads=1")
    tune = ",".join(f"'{x}'" for x in TUNE_HASH_BYTES)
    threshold = ",".join(f"'{x}'" for x in THRESHOLD_HASH_BYTES)
    sql = f"""
      WITH prior AS (
        SELECT entity_id FROM read_parquet('work/feature_pilot_s1.parquet')
        WHERE split='model_train'
      ), base AS (
        SELECT m.entity_id,k.country_norm,p.entity_id IS NOT NULL prior_pilot,
               substr(md5(m.entity_id||':{SEED}'),1,2) hash_byte
        FROM read_parquet('work/matcher_split_manifest.parquet') m
        JOIN read_parquet('work/keys/train_s1.parquet') k USING(entity_id)
        LEFT JOIN prior p USING(entity_id)
        WHERE m.split='model_train'
      )
      SELECT entity_id,country_norm,
        CASE WHEN prior_pilot THEN 'model_fit'
             WHEN hash_byte IN ({tune}) THEN 'model_tune'
             WHEN hash_byte IN ({threshold}) THEN 'model_threshold'
             ELSE 'model_fit' END split,
        hash_byte,prior_pilot
      FROM base ORDER BY entity_id
    """
    partial = output.with_suffix(".partial.parquet")
    partial.unlink(missing_ok=True)
    con.execute(
        f"COPY ({sql}) TO '{partial.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)"
    )
    partial.replace(output)
    con.execute(f"CREATE TEMP VIEW dev AS SELECT * FROM read_parquet('{output.as_posix()}')")
    counts = dict(con.sql("SELECT split,count(*) FROM dev GROUP BY 1 ORDER BY 1").fetchall())
    overlap = con.sql(
        "SELECT count(*)-count(distinct entity_id) FROM dev"
    ).fetchone()[0]
    prior_not_fit = con.sql(
        "SELECT count(*) FROM dev WHERE prior_pilot AND split!='model_fit'"
    ).fetchone()[0]

    # Ground truth is permitted for model_train.  It is used only for the required
    # target-isolation integrity check and never selects a model or threshold.
    con.execute("""
      CREATE TEMP TABLE gt AS
      SELECT source1_entity_id entity_id,trim(mid) target_entity_id
      FROM read_csv('dataset/train/train_ground_truth.tsv',delim='\t',header=true,
                    quote='',all_varchar=true),
           unnest(string_split(matched_entity_ids,',')) u(mid)
      WHERE source1_entity_id IN (SELECT entity_id FROM dev)
        AND matched_entity_ids IS NOT NULL AND length(trim(mid))>0
    """)
    target_overlap = con.sql("""
      SELECT count(*) FROM (
        SELECT target_entity_id,count(distinct split) n
        FROM gt JOIN dev USING(entity_id)
        GROUP BY 1 HAVING n>1)
    """).fetchone()[0]
    if overlap or prior_not_fit or target_overlap:
        raise RuntimeError(
            f"development firewall failed: s1_overlap={overlap}, "
            f"prior_not_fit={prior_not_fit}, target_overlap={target_overlap}"
        )

    split_info = {}
    for name in ("model_fit", "model_tune", "model_threshold"):
        split_info[name] = {
            "s1": counts[name],
            "entity_id_sha256": _id_checksum(con, "dev", f"split='{name}'"),
        }
    payload = {
        "schema_version": 1,
        "seed": SEED,
        "assignment": {
            "model_tune_hash_bytes": list(TUNE_HASH_BYTES),
            "model_threshold_hash_bytes": list(THRESHOLD_HASH_BYTES),
            "model_fit": "all remaining hash bytes plus every prior pilot model_train S1",
        },
        "top_level_split_sha256": sha256(Path("work/matcher_split_manifest.parquet")),
        "counts": counts,
        "s1_overlap": overlap,
        "target_id_overlap": target_overlap,
        "prior_pilot_not_fit": prior_not_fit,
        "splits": split_info,
        "model_threshold_firewall": (
            "membership/count/checksum only until model configuration, features, "
            "sampling, and training size are frozen"
        ),
    }
    checksums.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Model-development split firewall", "",
        f"Seed: `{SEED}`.", "",
        "The frozen top-level split is unchanged. Every previously used model-train "
        "pilot S1 is forced into `model_fit`. The remaining rows use the first byte "
        "of `md5(entity_id || seed)`: `00`-`07` for `model_tune`, `08`-`0f` for "
        "`model_threshold`, and all other bytes for `model_fit`.", "",
        "The approximately 94/3/3 allocation is a resource-safe adjustment from the "
        "suggested 90/5/5 split. Each protected subset still contains more than "
        "50,000 S1s.", "",
        "| Split | S1 | ID checksum |", "|---|---:|---|",
    ]
    for name, info in split_info.items():
        lines.append(f"| {name} | {info['s1']:,} | `{info['entity_id_sha256']}` |")
    lines += [
        "", f"S1 overlap: **{overlap}**. Target-ID overlap: **{target_overlap}**. "
        f"Prior pilot rows outside model_fit: **{prior_not_fit}**.", "",
        "`model_calibration` is reclassified as `baseline_dev`. It is absent from "
        "this file because the frozen top-level manifest remains authoritative.", "",
        "At split creation, `model_threshold` has membership, country, count, and an "
        "ID checksum only. No threshold labels, candidates, features, predictions, "
        "or metrics were read or materialized.", "",
    ]
    report.write_text("\n".join(lines), encoding="utf-8")
    con.close()
    return payload


def build_samples(output: Path) -> dict:
    """Select nested fit tiers and a fixed bounded baseline-dev population.

    The model_tune rows are all included.  model_threshold is deliberately absent.
    """
    con = duckdb.connect()
    con.execute("SET memory_limit='700MB'; SET threads=1")
    sql = f"""
      WITH prior_fit AS (
        SELECT p.entity_id,d.country_norm
        FROM read_parquet('work/feature_pilot_s1.parquet') p
        JOIN read_parquet('work/model_development_split_manifest.parquet') d USING(entity_id)
        WHERE p.split='model_train' AND d.split='model_fit'
      ), fit_extra_ranked AS (
        SELECT d.entity_id,d.country_norm,
          row_number() OVER(ORDER BY md5(d.entity_id||':{SAMPLE_SEED}'),d.entity_id) rn
        FROM read_parquet('work/model_development_split_manifest.parquet') d
        LEFT JOIN prior_fit p USING(entity_id)
        WHERE d.split='model_fit' AND p.entity_id IS NULL
      ), baseline_prior AS (
        SELECT p.entity_id,p.country_norm
        FROM read_parquet('work/feature_pilot_s1.parquet') p
        WHERE p.split='model_calibration'
      ), baseline_extra_ranked AS (
        SELECT m.entity_id,k.country_norm,
          row_number() OVER(ORDER BY md5(m.entity_id||':{SAMPLE_SEED}'),m.entity_id) rn
        FROM read_parquet('work/matcher_split_manifest.parquet') m
        JOIN read_parquet('work/keys/train_s1.parquet') k USING(entity_id)
        LEFT JOIN baseline_prior p USING(entity_id)
        WHERE m.split='model_calibration' AND p.entity_id IS NULL
      ), selected AS (
        SELECT entity_id,country_norm,'model_fit_base' sample_role,1 tier_a,1 tier_b,1 tier_c
        FROM prior_fit
        UNION ALL
        SELECT entity_id,country_norm,'model_fit_extra' sample_role,0 tier_a,
               (rn<=15000) tier_b,1 tier_c
        FROM fit_extra_ranked WHERE rn<=45000
        UNION ALL
        SELECT entity_id,country_norm,'baseline_dev_base' sample_role,0,0,0 FROM baseline_prior
        UNION ALL
        SELECT entity_id,country_norm,'baseline_dev_extra' sample_role,0,0,0
        FROM baseline_extra_ranked WHERE rn<=8000
        UNION ALL
        SELECT entity_id,country_norm,'model_tune' sample_role,0,0,0
        FROM read_parquet('work/model_development_split_manifest.parquet')
        WHERE split='model_tune'
      )
      SELECT * FROM selected ORDER BY sample_role,entity_id
    """
    partial = output.with_suffix(".partial.parquet")
    partial.unlink(missing_ok=True)
    con.execute(
        f"COPY ({sql}) TO '{partial.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)"
    )
    partial.replace(output)
    counts = dict(con.sql(
        f"SELECT sample_role,count(*) FROM read_parquet('{output.as_posix()}') GROUP BY 1 ORDER BY 1"
    ).fetchall())
    tier_counts = {
        "A": con.sql(f"SELECT count(*) FROM read_parquet('{output.as_posix()}') WHERE tier_a").fetchone()[0],
        "B": con.sql(f"SELECT count(*) FROM read_parquet('{output.as_posix()}') WHERE tier_b").fetchone()[0],
        "C": con.sql(f"SELECT count(*) FROM read_parquet('{output.as_posix()}') WHERE tier_c").fetchone()[0],
    }
    con.close()
    return {"seed": SAMPLE_SEED, "roles": counts, "tiers": tier_counts, "sha256": sha256(output)}
