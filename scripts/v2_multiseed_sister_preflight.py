"""Multi-seed, opposite-source sister expansion join preflight.

Phase 1 of V2.1 research plan.  Selects top-K seeds per S1 using
inference-available rank/IDF signals from the frozen V1 candidate set.
Seeds are selected with opposite-source diversity so that each S1
gets at most ceil(K/2) seeds from each target source.

NO GROUND-TRUTH labels are read; seed selection is inference-safe.
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

MAX_SEEDS_PER_S1 = 3
MAX_PER_SOURCE = 2          # At most 2 seeds from same source
DF_CAP = 1000               # Country-level document frequency cap for name 4-grams
TOP_GRAMS_PER_SEED = 4      # Rare grams per seed
HARD_JOIN_CAP = 200_000_000  # Abort if estimated join rows exceed this


def run():
    os.chdir(ROOT)
    research, sealed = populations()

    # Load name 4-gram DF from prior preflight
    ngram = json.loads((R / 'ngram_preflight.json').read_text())
    df = next(x for x in ngram['results'] if x['n'] == 4)
    if sha(ROOT / df['df_path']) != df['df_sha256']:
        raise RuntimeError('Full target name4 DF changed')

    c = connect('multiseed_preflight')

    # ── Select top-K seeds per S1 using inference-available signals ──────
    #
    # The seed selection uses exactly the signals available at inference:
    #   source_balanced_rank (lower = better)
    #   name_shared_idf (higher = rarer, better evidence)
    #   address_shared_idf (higher = better)
    #
    # Opposite-source diversity: within each S1, we rank seeds separately
    # per source, then interleave to get source-balanced coverage.
    seed_file = R / 'multiseed_top3.parquet'
    pending = seed_file.with_suffix('.pending.parquet')
    if pending.exists():
        pending.unlink()

    c.execute(f"""COPY (
      WITH per_source_ranked AS (
        SELECT source1_entity_id s1,
               target_entity_id seed_mid,
               substr(target_entity_id, 1, 2) target_source,
               source_balanced_rank sbr,
               name_shared_idf,
               address_shared_idf,
               row_number() OVER(
                 PARTITION BY source1_entity_id, substr(target_entity_id, 1, 2)
                 ORDER BY source_balanced_rank NULLS LAST,
                   name_shared_idf DESC NULLS LAST,
                   address_shared_idf DESC NULLS LAST,
                   target_entity_id
               ) source_rank
        FROM read_parquet('work/v2_research/v1_candidates/*.parquet')
      ),
      source_capped AS (
        SELECT * FROM per_source_ranked WHERE source_rank <= {MAX_PER_SOURCE}
      ),
      global_ranked AS (
        SELECT *,
          row_number() OVER(
            PARTITION BY s1
            ORDER BY source_rank,
              sbr NULLS LAST,
              name_shared_idf DESC NULLS LAST,
              address_shared_idf DESC NULLS LAST,
              seed_mid
          ) global_seed_rank
        FROM source_capped
      )
      SELECT s1, seed_mid, target_source, global_seed_rank::UTINYINT seed_rank
      FROM global_ranked
      WHERE global_seed_rank <= {MAX_SEEDS_PER_S1}
      ORDER BY s1, global_seed_rank
    ) TO '{pending.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
    os.replace(pending, seed_file)

    seed_stats = c.sql(f"""
      SELECT count(*) total_seeds,
             count(DISTINCT s1) s1_with_seeds,
             count(*) FILTER(WHERE seed_rank = 1) rank1,
             count(*) FILTER(WHERE seed_rank = 2) rank2,
             count(*) FILTER(WHERE seed_rank = 3) rank3,
             count(*) FILTER(WHERE target_source = 'S2') s2_seeds,
             count(*) FILTER(WHERE target_source = 'S3') s3_seeds
      FROM read_parquet('{seed_file.as_posix()}')
    """).fetchone()

    # ── Compute join-size estimate (same approach as single-seed preflight) ──
    c.execute("""CREATE TEMP VIEW targets AS
      SELECT entity_id mid, 's2' target_source, country_norm cc, name_norm nm
      FROM read_parquet('work/keys/train_s2.parquet')
      UNION ALL
      SELECT entity_id, 's3', country_norm, name_norm
      FROM read_parquet('work/keys/train_s3.parquet')""")

    expr = duckdb_grams_sql('nm', 4)
    c.execute(f"""CREATE TEMP TABLE selected AS
      WITH seed_grams AS (
        SELECT s.s1, s.seed_mid, s.target_source seed_source, t.cc, t.nm seed_name,
               s.seed_rank, {expr} gram
        FROM read_parquet('{seed_file.as_posix()}') s
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
      SELECT * FROM eligible WHERE key_rank <= {TOP_GRAMS_PER_SEED}""")

    est = c.sql("""
      WITH x AS (
        SELECT cc, gram, count(*) sn, max(df) dn FROM selected GROUP BY 1, 2
      )
      SELECT coalesce(sum(sn * dn), 0)::BIGINT,
             coalesce(max(sn * dn), 0)::BIGINT,
             count(*)
      FROM x
    """).fetchone()

    if est[0] > HARD_JOIN_CAP:
        raise RuntimeError(
            f'Multi-seed join estimate {est[0]:,} exceeds hard cap {HARD_JOIN_CAP:,}')

    selected_s1 = c.sql('SELECT count(DISTINCT s1) FROM selected').fetchone()[0]
    selected_seeds = c.sql('SELECT count(DISTINCT (s1, seed_mid)) FROM selected').fetchone()[0]

    result = {
        'status': 'LABEL_FREE_MULTI_SEED_PREFLIGHT',
        'seed_selection': (
            f'top {MAX_SEEDS_PER_S1} by per-source-rank interleave; '
            f'max {MAX_PER_SOURCE} per source; '
            'frozen V1 source_balanced_rank, name IDF, address IDF, target ID'
        ),
        'max_seeds_per_s1': MAX_SEEDS_PER_S1,
        'max_per_source': MAX_PER_SOURCE,
        'seed_file': seed_file.relative_to(ROOT).as_posix(),
        'seed_file_sha256': sha(seed_file),
        'total_seeds': seed_stats[0],
        's1_with_seeds': seed_stats[1],
        'rank1_seeds': seed_stats[2],
        'rank2_seeds': seed_stats[3],
        'rank3_seeds': seed_stats[4],
        's2_seeds': seed_stats[5],
        's3_seeds': seed_stats[6],
        'selected_s1_with_eligible_keys': selected_s1,
        'selected_seeds_with_eligible_keys': selected_seeds,
        'fourgram_df_cap': DF_CAP,
        'top_rare_grams_per_seed': TOP_GRAMS_PER_SEED,
        'estimated_same_country_all_source_join_rows_upper_bound': est[0],
        'worst_key_join_rows': est[1],
        'eligible_keys': est[2],
        'opposite_source_requirement_will_reduce_join': True,
        'full_target_rows': ngram['target_rows'],
        'labels_read': False,
        'name_df_sha256': df['df_sha256'],
        'baseline_candidates': 'frozen V1',
    }
    write_json(R / 'multiseed_preflight.json', result)
    log('multiseed_preflight_complete',
        command=r'.venv\Scripts\python.exe -B scripts\v2_multiseed_sister_preflight.py',
        v2_research_label_read=False,
        manifest_sha256=sha(R / 'multiseed_preflight.json'))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    with Monitor(R / 'tmp/multiseed_preflight') as m:
        run()
    write_json(R / 'multiseed_preflight_resources.json', m.result())
