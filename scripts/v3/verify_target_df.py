"""Verify existing complete-target token DF indexes and write a rebuild recipe."""
import argparse
import json
import time

import duckdb

from common import ROOT, OUT, execute_gate, sha, write_new


KEYS = ROOT / "work/keys"
DF_ROOT = ROOT / "work/freeze_gate"
SOURCES = [KEYS / "train_s2.parquet", KEYS / "train_s3.parquet"]


def run(args):
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to(ROOT.resolve()) or output.exists():
        raise FileExistsError("A new target-DF recipe path is required")
    normalization = ROOT / "code/business_entity_resolution/src/er/normalize.py"
    if not normalization.is_file():
        raise FileNotFoundError(normalization)

    con = duckdb.connect()
    con.execute("SET threads=1; SET memory_limit='2500MB'; SET preserve_insertion_order=false")
    temp = OUT / "tmp/target_df_recipe"
    temp.mkdir(parents=True, exist_ok=True)
    con.execute("SET temp_directory=?", [temp.as_posix()])
    con.execute("SET max_temp_directory_size='40GB'")
    source_sql = " UNION ALL ".join(
        f"SELECT entity_id,country_norm,name_nosuffix,addr_norm FROM read_parquet('{p.as_posix()}')"
        for p in SOURCES
    )
    rows, unique_ids = con.execute(
        f"SELECT count(*),count(DISTINCT entity_id) FROM ({source_sql})"
    ).fetchone()
    if rows != 10320219 or unique_ids != rows:
        raise RuntimeError(f"Unexpected complete target corpus: rows={rows}, unique={unique_ids}")

    checks = {}
    started = time.perf_counter()
    for field, column in (("name", "name_nosuffix"), ("addr", "addr_norm")):
        index = DF_ROOT / f"df_{field}.parquet"
        expected_sql = f"""SELECT country_norm cc,tok,count(DISTINCT entity_id)::BIGINT df
          FROM (SELECT entity_id,country_norm,unnest(str_split({column},' ')) tok FROM ({source_sql}))
          WHERE length(tok)>0 GROUP BY 1,2"""
        index_sql = f"SELECT cc,tok,df::BIGINT df FROM read_parquet('{index.as_posix()}')"
        countries = [row[0] for row in con.execute(
            f"SELECT DISTINCT cc FROM ({index_sql}) ORDER BY cc"
        ).fetchall()]
        expected_countries = [row[0] for row in con.execute(
            f"SELECT DISTINCT country_norm FROM ({source_sql}) ORDER BY 1"
        ).fetchall()]
        if countries != expected_countries:
            raise RuntimeError(f"{field} country coverage mismatch: {countries} != {expected_countries}")
        rows_expected = rows_index = duplicates = missing = changed = extra = 0
        for country in countries:
            actual_country = f"SELECT cc,tok,df FROM ({index_sql}) WHERE cc=?"
            expected_country = f"SELECT cc,tok,df FROM ({expected_sql}) WHERE cc=?"
            country_expected, country_index = con.execute(
                f"SELECT (SELECT count(*) FROM ({expected_country})),(SELECT count(*) FROM ({actual_country}))",
                [country, country],
            ).fetchone()
            country_duplicates = con.execute(
                f"SELECT count(*)-count(DISTINCT tok) FROM ({actual_country})", [country]
            ).fetchone()[0]
            country_missing = con.execute(
                f"SELECT count(*) FROM ({expected_country}) e ANTI JOIN ({actual_country}) a USING(cc,tok)",
                [country, country],
            ).fetchone()[0]
            country_changed = con.execute(
                f"SELECT count(*) FROM ({expected_country}) e JOIN ({actual_country}) a USING(cc,tok) WHERE e.df<>a.df",
                [country, country],
            ).fetchone()[0]
            country_extra = con.execute(
                f"SELECT count(*) FROM ({actual_country}) a ANTI JOIN ({expected_country}) e USING(cc,tok)",
                [country, country],
            ).fetchone()[0]
            rows_expected += country_expected
            rows_index += country_index
            duplicates += country_duplicates
            missing += country_missing
            changed += country_changed
            extra += country_extra
        if duplicates or rows_expected != rows_index or missing or changed or extra:
            raise RuntimeError(f"{field} DF index mismatch: " + json.dumps({
                "duplicates": duplicates, "expected_keys": rows_expected,
                "index_keys": rows_index, "missing": missing,
                "changed_df": changed, "extra": extra}))
        checks[field] = {
            "path": index.relative_to(ROOT).as_posix(), "sha256": sha(index),
            "keys": rows_index, "complete": True, "duplicates": 0,
            "missing": 0, "changed_df": 0, "extra": 0,
        }
        print(f"verified {field}: {rows_index:,} complete (country,token) rows", flush=True)
    con.close()

    result = {
        "status": "VERIFIED_COMPLETE_TARGET_RECIPE",
        "labels_used": False,
        "target_entity_rows": rows,
        "target_entity_ids_unique": True,
        "target_entity_sources": [p.relative_to(ROOT).as_posix() for p in SOURCES],
        "target_source_sha256": {p.relative_to(ROOT).as_posix(): sha(p) for p in SOURCES},
        "indexes": list(checks.values()),
        "normalization_path": normalization.relative_to(ROOT).as_posix(),
        "normalization_sha256": sha(normalization),
        "tokenization": "DuckDB str_split(normalized_value,' '), unnest; discard empty token; group by country_norm and token; count distinct target entity_id",
        "fields": {"name": "name_nosuffix", "addr": "addr_norm"},
        "country_rule": "country_norm is part of each document-frequency key; never pool countries",
        "coverage": "Every non-empty normalized token in every unique S2/S3 training target appears exactly once per country and has its exact distinct-entity document frequency.",
        "text_length_rules": "Use the frozen normalizer without truncation. Candidate retrieval may independently bound generated queries, but this DF recipe does not truncate normalized text.",
        "rebuild_command": ".venv\\Scripts\\python.exe -B scripts\\v3\\verify_target_df.py --execute --output work/v3_research_r1/target_df_recipe_rebuilt.json",
        "rebuild_sql": "WITH target AS (SELECT entity_id,country_norm,name_nosuffix,addr_norm FROM train_s2 UNION ALL SELECT entity_id,country_norm,name_nosuffix,addr_norm FROM train_s3), tokens AS (SELECT entity_id,country_norm cc,unnest(str_split(field,' ')) tok FROM target) SELECT cc,tok,count(DISTINCT entity_id)::BIGINT df FROM tokens WHERE length(tok)>0 GROUP BY 1,2",
        "verification_runtime_seconds": time.perf_counter() - started,
        "verification_method": "independently recompute complete DF key sets and counts from all 10,320,219 unique S2/S3 target records; compare missing, extra, duplicate and changed counts",
    }
    write_new(output, result)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", default="work/v3_research_r1/target_df_recipe.json")
    args = parser.parse_args()
    execute_gate(args)
    run(args)
