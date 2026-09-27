"""Multi-seed, opposite-source, label-free sister expansion — materialization.

Phase 1 of V2.1 research plan.  For each seed selected by the multi-seed
preflight, finds opposite-source targets in the same country whose name
shares at least one 4-gram with the seed.  Candidates are ranked by
IDF-weighted gram evidence and capped at QUOTA per S1.

NO GROUND-TRUTH labels are read anywhere in the expansion logic.
Seeds are selected purely by V1-candidate rank/IDF signals.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code/business_entity_resolution/src'))
from er.candidates_v2.ngram import duckdb_grams_sql
from er.candidates_v2.runtime import R, W, sha, write_json, log, connect, populations, Monitor

PASS = 'multiseed_sister'
DF_CAP = 1000
TOP_GRAMS = 4
QUOTA = 15           # Per S1, not per seed — merge all seeds then rank
SHARDS = '0123456789abcdef'


def run():
    os.chdir(ROOT)
    research, sealed = populations()

    # Verify the preflight
    preflight = json.loads((R / 'multiseed_preflight.json').read_text())
    if preflight['status'] != 'LABEL_FREE_MULTI_SEED_PREFLIGHT':
        raise PermissionError('Multi-seed preflight not complete')
    if sha(ROOT / preflight['seed_file']) != preflight['seed_file_sha256']:
        raise PermissionError('Seed file changed since preflight')

    ngram = json.loads((R / 'ngram_preflight.json').read_text())
    df = next(x for x in ngram['results'] if x['n'] == 4)
    if sha(ROOT / df['df_path']) != df['df_sha256']:
        raise RuntimeError('Full target name4 DF changed')

    est_join = preflight['estimated_same_country_all_source_join_rows_upper_bound']
    hard_cap = 200_000_000
    if est_join > hard_cap:
        raise RuntimeError(f'Estimated join {est_join:,} exceeds hard cap {hard_cap:,}')

    # Use higher memory limit than default 700MB for the larger multi-seed join
    import duckdb
    tmp_dir = R / 'tmp' / PASS
    tmp_dir.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.execute("SET threads=1; SET memory_limit='1500MB'; SET preserve_insertion_order=false")
    c.execute('SET temp_directory=?', [tmp_dir.as_posix()])
    c.execute("SET max_temp_directory_size='24GB'")

    # ── Build target corpus view ────────────────────────────────────────
    c.execute("""CREATE TEMP VIEW targets AS
      SELECT entity_id mid, 's2' target_source, country_norm cc, name_norm nm
      FROM read_parquet('work/keys/train_s2.parquet')
      UNION ALL
      SELECT entity_id, 's3', country_norm, name_norm
      FROM read_parquet('work/keys/train_s3.parquet')""")

    expr = duckdb_grams_sql('nm', 4)

    # ── Build selected gram table from all seeds ─────────────────────────
    c.execute(f"""CREATE TEMP TABLE selected AS
      WITH seed_grams AS (
        SELECT s.s1, s.seed_mid, s.target_source seed_source, t.cc,
               t.nm seed_name, {expr} gram
        FROM read_parquet('{preflight["seed_file"]}') s
        JOIN targets t ON s.seed_mid = t.mid
        WHERE length(t.nm) BETWEEN 4 AND 64
      ),
      eligible AS (
        SELECT g.*, d.df,
          row_number() OVER(
            PARTITION BY s1, seed_mid ORDER BY d.df, g.gram
          ) key_rank
        FROM seed_grams g
        JOIN read_parquet('{df["df_path"]}') d ON g.cc = d.cc AND g.gram = d.gram
        WHERE d.df <= {DF_CAP}
      )
      SELECT * FROM eligible WHERE key_rank <= {TOP_GRAMS}""")

    # Verify join estimate matches preflight
    est = c.sql("""
      WITH x AS (SELECT cc, gram, count(*) sn, max(df) dn FROM selected GROUP BY 1, 2)
      SELECT coalesce(sum(sn * dn), 0)::BIGINT, coalesce(max(sn * dn), 0)::BIGINT FROM x
    """).fetchone()
    if est[0] != est_join:
        raise RuntimeError(f'Join estimate {est[0]} != preflight {est_join}')
    print(f'multi-seed upper-bound joins: {est}', flush=True)

    # ── Build target postings (reuse structure from single-seed) ──────
    posting = R / f'{PASS}_postings.parquet'
    receipt_path = R / f'{PASS}_postings_receipt.json'
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if not posting.exists() or sha(posting) != receipt['sha256']:
            raise RuntimeError('Multi-seed posting receipt mismatch')
    else:
        pending = posting.with_suffix('.pending.parquet')
        if pending.exists():
            pending.unlink()
        c.execute(f"""COPY (
          SELECT g.mid, g.target_source, g.cc, g.nm target_name, g.gram
          FROM (
            SELECT mid, target_source, cc, nm,
                   {expr} gram
            FROM targets
            WHERE length(nm) BETWEEN 4 AND 64
          ) g
          JOIN (SELECT DISTINCT cc, gram FROM selected) k USING(cc, gram)
        ) TO '{pending.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
        os.replace(pending, posting)
        n = c.sql(f"SELECT count(*) FROM read_parquet('{posting.as_posix()}')").fetchone()[0]
        write_json(receipt_path, {
            'status': 'COMPLETE_MULTISEED_SISTER_POSTINGS',
            'path': posting.relative_to(ROOT).as_posix(),
            'sha256': sha(posting), 'rows': n,
            'full_target_rows': ngram['target_rows']
        })
        print(f'multi-seed target postings: {n}', flush=True)

    # ── Shard expansion joins ──────────────────────────────────────────
    directory = R / f'{PASS}_candidates'
    directory.mkdir(exist_ok=True)
    parts = []
    for shard in SHARDS:
        out = directory / f'{shard}.parquet'
        rp = directory / f'{shard}.json'
        if rp.exists():
            receipt = json.loads(rp.read_text())
            if not out.exists() or sha(out) != receipt['sha256']:
                raise RuntimeError(f'Multi-seed sister shard receipt mismatch: {shard}')
            parts.append(receipt)
            continue
        if out.exists():
            raise RuntimeError(f'Unreceipted multi-seed sister shard: {out}')
        pending = directory / f'{shard}.pending.parquet'
        if pending.exists():
            pending.unlink()

        # Join seeds → postings → rank per S1 (merging across seeds)
        c.execute(f"""COPY (
          WITH hits AS (
            SELECT s.s1, p.mid, p.target_source,
                   count(*)::UTINYINT shared_grams,
                   sum(ln(1.0 + {ngram['target_rows']}::DOUBLE / s.df)) evidence,
                   any_value(s.seed_name) seed_name,
                   any_value(p.target_name) target_name,
                   -- Track how many distinct seeds contributed
                   count(DISTINCT s.seed_mid)::UTINYINT contributing_seeds
            FROM selected s
            JOIN read_parquet('{posting.as_posix()}') p
              ON s.cc = p.cc AND s.gram = p.gram
            WHERE substr(md5(s.s1), 1, 1) = '{shard}'
              AND p.target_source <> s.seed_source     -- opposite source only
              AND p.mid <> s.seed_mid                  -- not the seed itself
            GROUP BY 1, 2, 3
          ),
          ranked AS (
            SELECT *,
              row_number() OVER(
                PARTITION BY s1
                ORDER BY contributing_seeds DESC,
                         evidence DESC,
                         shared_grams DESC,
                         jaro_winkler_similarity(seed_name, target_name) DESC,
                         mid
              ) s1_rank
            FROM hits
          )
          SELECT s1 source1_entity_id,
                 mid target_entity_id,
                 target_source,
                 shared_grams,
                 evidence::FLOAT sister_name_evidence,
                 contributing_seeds,
                 s1_rank::USMALLINT source_rank,
                 512::USMALLINT provenance
          FROM ranked
          WHERE s1_rank <= {QUOTA}
          ORDER BY source1_entity_id, target_entity_id
        ) TO '{pending.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
        os.replace(pending, out)
        n = c.sql(f"SELECT count(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0]
        receipt = {
            'shard': shard,
            'path': out.relative_to(ROOT).as_posix(),
            'sha256': sha(out),
            'rows': n,
        }
        write_json(rp, receipt)
        parts.append(receipt)
        print(f'multi-seed sister shard {shard}: {n}', flush=True)

    # ── Manifest ─────────────────────────────────────────────────────────
    config = {
        'stage_count': 1,
        'max_seeds_per_s1': preflight['max_seeds_per_s1'],
        'max_per_source': preflight['max_per_source'],
        'seed_policy': preflight['seed_selection'],
        'opposite_target_source_only': True,
        'name_chargram_n': 4,
        'country_target_df_cap': DF_CAP,
        'top_rare_grams_per_seed': TOP_GRAMS,
        'per_s1_expansion_quota': QUOTA,
        'hard_intermediate_join_cap': hard_cap,
        'country_partitioned': True,
        'physical_shards': 'first hexadecimal md5(S1 ID)',
        'provenance_bit': 512,
        'rank': (
            'contributing_seeds DESC, '
            'sum log(1 + complete_target_rows/country_target_DF), '
            'shared grams, seed-target name similarity, target ID'
        ),
    }
    manifest = {
        'status': 'GENERATED_CHECKSUMMED_PENDING_AUDIT',
        'pass': PASS,
        'config': config,
        'policy_config_sha256': hashlib.sha256(
            json.dumps(config, sort_keys=True).encode()
        ).hexdigest(),
        'preflight_sha256': sha(R / 'multiseed_preflight.json'),
        'seed_file_sha256': preflight['seed_file_sha256'],
        'target_rows': ngram['target_rows'],
        'posting_receipt_sha256': sha(receipt_path),
        'estimated_join_rows_upper_bound': est[0],
        'worst_key_join_rows': est[1],
        'parts': parts,
        'rows': sum(x['rows'] for x in parts),
        'research_s1': len(research),
        'labels_read': False,
    }
    write_json(R / f'{PASS}_manifest.json', manifest)
    log('multiseed_sister_materialization_complete',
        command=r'.venv\Scripts\python.exe -B scripts\v2_multiseed_sister_materialize.py',
        v2_research_label_read=False,
        manifest_sha256=sha(R / f'{PASS}_manifest.json'))
    print(f"Total multi-seed candidates: {manifest['rows']:,}")


if __name__ == '__main__':
    with Monitor(R / 'tmp/multiseed_sister') as m:
        run()
    write_json(R / f'{PASS}_resources.json', m.result())
