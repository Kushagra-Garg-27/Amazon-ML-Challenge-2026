"""Render the completed Phase 2 research evidence without opening row-level labels."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'work/v2_phase2_r1'
PLAN_ORIGINAL_SHA = 'FED8357C75E277A508FBFC57E1D460E0D07421E4355C553B53B20CB513F6CBAE'


def load(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding='utf-8'))


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            h.update(block)
    return h.hexdigest()


def f(x, digits=6):
    if x is None:
        return 'N/A'
    if isinstance(x, bool):
        return 'yes' if x else 'no'
    if isinstance(x, int):
        return f'{x:,}'
    return f'{x:.{digits}f}'


def table(head, rows):
    return '\n'.join(['| ' + ' | '.join(head) + ' |',
                      '| ' + ' | '.join(['---'] * len(head)) + ' |'] +
                     ['| ' + ' | '.join(map(str, row)) + ' |' for row in rows])


def inventory() -> dict:
    records = []
    for path in sorted(OUT.rglob('*')):
        if not path.is_file() or 'tmp' in path.relative_to(OUT).parts:
            continue
        if path.name in ('report.md', 'artifact_inventory.json') or '.pending.' in path.name:
            continue
        row = {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path),
               'bytes': path.stat().st_size, 'population': 'v2_candidate_research research artifacts'}
        if path.suffix == '.parquet':
            row['row_count'] = pq.read_metadata(path).num_rows
        elif path.suffix == '.json':
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    for field in ('rows', 'candidate_count', 'duplicate_count', 'invalid_id_count',
                                  'configuration', 'provenance_metadata', 'population'):
                        if field in data:
                            row[field] = data[field]
            except ValueError:
                pass
        records.append(row)
    output = {'status': 'CHECKSUMMED', 'scope': 'work/v2_phase2_r1 excluding temporary files, report, and this inventory',
              'file_count': len(records), 'files': records}
    (OUT / 'artifact_inventory.json').write_text(json.dumps(output, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return output


def frontier(policies, metric):
    best = float('-inf')
    kept = []
    for name, row in sorted(policies.items(), key=lambda x: (x[1]['count'], x[0])):
        if row[metric] > best + 1e-12:
            kept.append((name, row))
            best = row[metric]
    return kept


def run():
    s, p, e, c, r, t = (load(x) for x in ('step0_evaluation.json', 'neighbor_preflight.json',
        'expansion_evaluation.json', 'cap_evaluation.json', 'resource_projection_with_generation.json',
        'test_results_final.json'))
    if any(x['status'] != 'COMPLETE' for x in (s, e, c)):
        raise RuntimeError('Phase 2 evaluations incomplete')
    if sha(OUT / 'strategic_plan_context_original.md').upper() != PLAN_ORIGINAL_SHA:
        raise RuntimeError('Strategic plan context copy changed')
    inv = inventory()
    inv_sha = sha(OUT / 'artifact_inventory.json')
    ex = e['configurations']
    caps = c['configurations']
    best_ex_name, best_ex = max(ex.items(), key=lambda x: x[1]['primary_frozen_v1']['macro_f05'])
    best_cap_name, best_cap = max(caps.items(), key=lambda x: x[1]['frozen_matcher']['macro_f05'])
    vx, px = s['v1'], s['v1_plus_all']
    lines = []
    add = lines.append
    add('# Amazon ML Challenge 2026 — V2 Phase 2 research report')
    add('')
    add('**Scope:** frozen V1 matcher on the 100,000-S1 `v2_candidate_research` population; one-hop matcher-seeded expansion and secondary cap/quota contrasts. No model training, test inference, or submission.')
    add(f'**Strategic context:** original root plan SHA-256 `{PLAN_ORIGINAL_SHA}`; exact original copy `strategic_plan_context_original.md`. Updated root plan SHA-256 `{sha(ROOT / "v2_strategic_research_plan.md")}` records measured Phase 2 corrections without replacing historical measurements.')
    add(f'**Artifact inventory:** `artifact_inventory.json` SHA-256 `{inv_sha}`, covering {inv["file_count"]} non-temporary Phase 2 files. Per-part manifests contain configurations, counts, provenance and SHA-256.')
    add('**Firewall finding:** An initial Phase 2 guard decoded row-level membership IDs for sealed populations once. It read no sealed labels. This violates the requested zero sealed-population row-access condition; later Phase 2 runs use a research-only guard. The human eligibility gate therefore cannot pass as written.')
    add('')
    add('## 1. Step 0: end-to-end baselines')
    add('')
    keys = [('Research S1', 's1'), ('Predictions', 'predicted'), ('True positives', 'true_positive'),
            ('Macro F0.5', 'macro_f05'), ('Pair precision', 'pair_precision'), ('Pair recall', 'pair_recall'),
            ('Candidate pair recall', 'candidate_pair_recall'), ('Candidate oracle F0.5', 'candidate_oracle_macro_f05'),
            ('Singleton accuracy', 'singleton_accuracy'), ('Empty prediction rate', 'empty_prediction_rate'),
            ('Mean predictions/S1', 'mean_predicted_matches'), ('Multi-match underprediction', 'multi_match_underprediction'),
            ('Multi-match overprediction', 'multi_match_overprediction')]
    add(table(['Metric', 'V1 frozen candidates', 'v1_plus_all'],
        [(label, f(vx[key]), f(px[key])) for label, key in keys]))
    add('')
    step0_b = s['paired_bootstrap']
    add(f'Paired 1,000-resample S1 bootstrap (seed {step0_b["seed"]}): `v1_plus_all − V1` = **{f(step0_b["point_delta"])}**, 95% CI **[{f(step0_b["ci_95"][0])}, {f(step0_b["ci_95"][1])}]**, excludes zero: **{f(step0_b["ci_excludes_zero"])}**. Of {f(s["new_gt_links"])} newly retrieved GT links, the frozen matcher accepted {f(s["new_gt_accepted"])} ({f(s["new_gt_acceptance_fraction"] * 100, 2)}%).')
    add('')
    add('## 2. Inference-only target-neighbor index and preflight')
    add('')
    add(f'The complete training S2+S3 target corpus contains {f(p["complete_target_corpus_rows"])} targets. Seed targets come only from frozen V1 scores: {f(p["seed_pair_count_061"])} at 0.61, {f(p["seed_pair_count_080"])} at 0.80, and {f(p["seed_pair_count_095"])} at 0.95; {f(p["s1_with_seeds_061"])} S1s have a 0.61 seed. The index uses same-country rare normalized name 4-grams, at most four keys per seed target, explicit DF caps, and at most 20 neighbors per source. Self-neighbors and duplicate IDs are excluded; source direction is retained. Ordering is deterministic: shared grams, evidence, then target ID; cross-source neighbors have priority in each seed quota.')
    add('')
    add(table(['DF cap', 'Selected seed/key rows', 'Seed targets with keys', 'Estimated join rows',
               'Worst shard', 'Postings', 'Neighbor rows', 'Index wall s', 'Peak RSS GB', 'Peak temp GB'],
        [(f(cap), f(p['caps'][str(cap)]['selected_rows']), f(p['caps'][str(cap)]['seed_targets_with_keys']),
          f(p['caps'][str(cap)]['estimated_join_rows']), f(p['caps'][str(cap)]['worst_shard_join_rows']),
          f(load(f'neighbor_postings_df{cap}.json')['rows']),
          f(load(f'neighbor_index_df{cap}/manifest.json')['rows']),
          f(load(f'neighbor_index_df{cap}_resources.json')['wall_seconds'], 1),
          f(load(f'neighbor_index_df{cap}_resources.json')['sampled_peak_process_tree_rss_bytes']/1e9, 2),
          f(load(f'neighbor_index_df{cap}_resources.json')['sampled_peak_temp_disk_bytes']/1e9, 2))
         for cap in (1000, 5000)]))
    add('')
    add('Preflight rejected unbounded joins using a 1.2B total and 100M per-shard join guard. Both DF settings stayed under those bounds. Full country DF distributions and worst keys are in `neighbor_preflight.json`. No GT was used for seeds, keys, index, source direction, tie-breaks, DF caps, or quotas. Target-neighbor relevance is evaluated downstream against research GT only after the grid artifacts were checksummed and audited.')
    add('')
    add('## 3. Complete one-hop matcher-seeded grid')
    add('')
    add('Every configuration below uses the audited one-hop artifact, deduplicates against `v1_plus_all`, and has been rescored by the same frozen V1 feature/model/0.61 decision pipeline. “GT” and all score results use research labels only after candidate checksums and structural audits.')
    add('')
    head = ['Seed / quota / DF', 'Expansion pairs', 'Already baseline', 'New pairs', 'Mean new/S1',
            'P95/P99/max union/S1', 'All expansion GT', 'GT already baseline', 'Net GT', 'GT/1k new',
            'Pair recall', 'Oracle F0.5', 'Sister opportunities']
    rows = []
    for name, x in ex.items():
        rows.append((name.replace('matcher_seed_t', '').replace('_q', ' / ').replace('_df', ' / '),
          f(x['expansion_candidates']), f(x['already_in_plus_all']), f(x['genuinely_new_candidates']),
          f(x['genuinely_new_candidates']/100000, 2),
          '/'.join(f(x[z], 0) for z in ('p95_union_candidates_per_s1','p99_union_candidates_per_s1','max_union_candidates_per_s1')),
          f(x['expansion_gt_recovered']), f(x['expansion_gt_already_in_plus_all']),
          f(x['new_gt_beyond_plus_all']), f(x['new_gt_per_1000_genuine_candidates'], 3),
          f(x['candidate_pair_recall']), f(x['candidate_oracle_macro_f05']),
          f(x['diagnostic_sister_opportunities_recovered']) + ' / 37,866'))
    add(table(head, rows))
    add('')
    add('Direction and seed propagation by configuration: the four direction columns are expansion-pair counts; same and cross-source counts are kept separate. False-seed propagation is the share of false seeds with at least one edge, not a final false-positive rate.')
    add('')
    head = ['Seed / quota / DF', 'S2→S2', 'S2→S3', 'S3→S2', 'S3→S3', 'True/false seeds',
            'Edges per true/false seed', 'False-seed propagation', 'Expansion candidate precision']
    rows = []
    for name, x in ex.items():
        d = {y['direction']: y['expansion_pairs'] for y in x['source_directions']}
        a = x['false_seed_audit']
        rows.append((name.replace('matcher_seed_t', '').replace('_q', ' / ').replace('_df', ' / '),
          *(f(d.get(z, 0)) for z in ('S2→S2','S2→S3','S3→S2','S3→S3')),
          f(a['true_seeds'])+'/'+f(a['false_seeds']),
          f(a['candidate_edges_per_true_seed'], 2)+'/'+f(a['candidate_edges_per_false_seed'], 2),
          f(a['false_seed_propagation_rate'], 4),
          f(x['candidate_precision_expansion_only'], 6)))
    add(table(head, rows))
    add('')
    add('## 4. Frozen-matcher end-to-end and research-tuned decisions')
    add('')
    head = ['Seed / quota / DF', 'Macro F0.5', 'Δ vs plus_all', 'Paired 95% CI', 'Precision',
            'Recall', 'TP added', 'FP added', 'Singleton', 'Empty', 'Mean matches', 'Multi under/over']
    rows = []
    for name, x in ex.items():
        m = x['primary_frozen_v1']; b = m['paired_bootstrap_vs_plus_all']
        rows.append((name.replace('matcher_seed_t', '').replace('_q', ' / ').replace('_df', ' / '),
          f(m['macro_f05']), f(b['point_delta']),
          '['+f(b['ci_95'][0])+', '+f(b['ci_95'][1])+']',
          f(m['pair_precision']), f(m['pair_recall']),
          f(m['final_true_positives_introduced']), f(m['final_false_positives_introduced']),
          f(m['singleton_accuracy']), f(m['empty_prediction_rate']),
          f(m['mean_predicted_matches'], 4),
          f(m['multi_match_underprediction'])+'/'+f(m['multi_match_overprediction'])))
    add(table(head, rows))
    add('')
    add('The lower decision thresholds apply only to candidates linked to accepted seeds. These are **RESEARCH-TUNED / OPTIMISTIC** and are separate from the primary frozen 0.61 result. They were tested at the predeclared quota-20 points.')
    add('')
    rows = []
    for name, x in ex.items():
        if not x['research_tuned_optimistic']:
            continue
        for threshold in ('0.3','0.45','0.61'):
            m = x['research_tuned_optimistic'][threshold]
            b = m['paired_bootstrap_vs_plus_all']
            rows.append((name, threshold, f(m['macro_f05']), f(b['point_delta']),
                '['+f(b['ci_95'][0])+', '+f(b['ci_95'][1])+']',
                f(m['pair_precision']), f(m['pair_recall']),
                f(m['final_true_positives_introduced']), f(m['final_false_positives_introduced'])))
    add(table(['Configuration', 't2', 'Macro F0.5', 'Δ vs plus_all', 'Paired 95% CI',
               'Precision', 'Recall', 'TP added', 'FP added'], rows))
    add('')
    add(f'**Best primary one-hop result:** `{best_ex_name}` yields {f(best_ex["primary_frozen_v1"]["macro_f05"])} macro F0.5 and a paired delta of {f(best_ex["primary_frozen_v1"]["paired_bootstrap_vs_plus_all"]["point_delta"])} versus `v1_plus_all`. Its candidate-only precision is {f(best_ex["candidate_precision_expansion_only"])}; the frozen matcher accepts {f(best_ex["primary_frozen_v1"]["final_true_positives_introduced"])} true and {f(best_ex["primary_frozen_v1"]["final_false_positives_introduced"])} false new pairs. The distinction between candidate risk and final prediction risk is material.')
    add('')
    add('**Two-hop:** Not materialized. The one-hop DF-5,000 index required 1.021B estimated joins and 23 minutes, while a simple 20-first-hop × 40-neighbor bound gives 228.4M raw second-hop paths for the 285,545 research seeds before deduplication. A second hop would require target-index coverage beyond the frozen seed targets and a separate bounded preflight; resource feasibility has not been established. No two-hop score is inferred.')
    add('')
    add('## 5. Secondary cap and quota sweep')
    add('')
    add(f'Deduplicated GT misses after `v1_plus_all`: **{f(c["remaining_gt_misses_after_plus_all"])}**. Of the historical V1 rank/cap miss category, **{f(c["v1_rank_cap_category_remaining_after_plus_all"])}** remain uncovered by `v1_plus_all`; prior category counts must not be added together. The sweep compares individual heavy-cap, source-quota and name4-quota changes plus two combined extremes, without a full Cartesian product. All six artifacts were audited before research GT access.')
    add('')
    rows = []
    for name, x in caps.items():
        m = x['frozen_matcher']; b = m['paired_bootstrap_vs_plus_all']; z = r['policies'][name]
        rows.append((name, f(x['added_candidates']), f(x['union_candidate_count']),
          f(x['new_gt_beyond_plus_all']), f(x['new_gt_per_1000_added'], 3),
          f(x['candidate_pair_recall']), f(x['candidate_oracle_macro_f05']),
          '/'.join(f(x[k],0) for k in ('p95_union_candidates_per_s1','p99_union_candidates_per_s1','max_union_candidates_per_s1')),
          f(m['macro_f05']), f(b['point_delta']),
          '['+f(b['ci_95'][0])+', '+f(b['ci_95'][1])+']',
          f(z['projected_test_candidates']), f(z['full_runtime_ratio_vs_measured_v1'], 3)))
    add(table(['Configuration', 'New pairs', 'Total pairs', 'Net GT', 'GT/1k', 'Pair recall',
      'Oracle F0.5', 'P95/P99/max union', 'End-to-end F0.5', 'Δ vs plus_all',
      'Paired 95% CI', 'Projected test pairs', 'Runtime × V1'], rows))
    add('')
    add(f'Best tested cap macro F0.5 is `{best_cap_name}` at {f(best_cap["frozen_matcher"]["macro_f05"])}. This is a measured research result; its resource and firewall gates are assessed separately.')
    add('')
    add('## 6. Candidate and end-to-end Pareto frontiers')
    add('')
    policies = {'V1': {'count':15649461,'pair':vx['candidate_pair_recall'],
                       'oracle':vx['candidate_oracle_macro_f05'],'end':vx['macro_f05']},
                'v1_plus_all': {'count':18895613,'pair':px['candidate_pair_recall'],
                       'oracle':px['candidate_oracle_macro_f05'],'end':px['macro_f05']}}
    for name, x in ex.items():
        policies[name] = {'count':x['union_candidate_count'], 'pair':x['candidate_pair_recall'],
                          'oracle':x['candidate_oracle_macro_f05'],
                          'end':x['primary_frozen_v1']['macro_f05']}
    for name, x in caps.items():
        policies[name] = {'count':x['union_candidate_count'], 'pair':x['candidate_pair_recall'],
                          'oracle':x['candidate_oracle_macro_f05'],
                          'end':x['frozen_matcher']['macro_f05']}
    for label, key in (('Candidate pair-recall frontier','pair'),('End-to-end F0.5 frontier','end')):
        add(f'### {label}')
        add('')
        add(table(['Policy','Research pairs','Pair recall','Oracle F0.5','End-to-end F0.5'],
          [(name, f(x['count']), f(x['pair']), f(x['oracle']), f(x['end'])) for name, x in frontier(policies,key)]))
        add('')
    add('The candidate oracle is a diagnostic upper bound for a fixed candidate set; the frozen matcher result is the operational end-to-end measure. Higher oracle values do not imply the same end-to-end gain.')
    add('')
    add('## 7. Slices and multi-match behavior')
    add('')
    focus = [('V1',vx),('v1_plus_all',px),('best one-hop',best_ex['primary_frozen_v1']),
             ('best cap',best_cap['frozen_matcher'])]
    for field, title in (('target_source','S2/S3 truth-link recall'),('country','India/US truth-link recall'),
                         ('both_address','Address present/missing truth-link recall'),
                         ('script_relation','Script-relation truth-link recall')):
        add(f'### {title}')
        add('')
        rows = []
        for policy, item in focus:
            for z in item['truth_link_slices'][field]:
                rows.append((policy,str(z[field]),f(z['gt_links']),f(z['accepted']),f(z['recall'])))
        add(table(['Policy','Slice','Truth links','Accepted','Recall'], rows))
        add('')
    grouped_script = []
    for policy, item in focus:
        for label, test in (('same or empty',lambda code: code.split(':')[0] == code.split(':')[1] or '0' in code.split(':')),
                            ('script conflict',lambda code: code.split(':')[0] != code.split(':')[1] and '0' not in code.split(':'))):
            chosen = [z for z in item['truth_link_slices']['script_relation'] if test(z['script_relation'])]
            truth = sum(z['gt_links'] for z in chosen)
            accepted = sum(z['accepted'] for z in chosen)
            grouped_script.append((policy,label,f(truth),f(accepted),f(accepted/truth)))
    add('The verified script-class feature contract defines zero as empty and different nonzero classes as conflict. Aggregating the recorded relation codes gives:')
    add('')
    add(table(['Policy','Script group','Truth links','Accepted','Recall'],grouped_script))
    add('')
    add(table(['Policy','India S1 F0.5','US S1 F0.5','Singleton accuracy',
               'Empty rate','Mean matches/S1','Multi under/over'],
        [(name,f(next(z['macro_f05'] for z in item['s1_slices']['country'] if z['country']=='india')),
          f(next(z['macro_f05'] for z in item['s1_slices']['country'] if z['country']=='us')),
          f(item['singleton_accuracy']),f(item['empty_prediction_rate']),
          f(item['mean_predicted_matches'],4),
          f(item['multi_match_underprediction'])+'/'+f(item['multi_match_overprediction']))
         for name,item in focus]))
    add('')
    add('The research S1 address-missing indicator contains only `false`; its S1-level missing-address contrast cannot be estimated. The truth-link `both_address` slice above provides the available address-present/missing comparison.')
    add('')
    add('## 8. Full-test resource projection and gate evidence')
    add('')
    add(f'The projection scales research candidate density from 100,000 S1 to 1,732,544 test S1, with `v1_plus_all` as the explicit baseline. The measured V1 test candidate/feature/score stage envelope is {f(r["measured_v1_test_stage_runtime_seconds"],1)} seconds from historical aggregate receipts only; no test rows were accessed. One-time complete-target index cost plus research expansion-grid runtime scaled by S1 density is added to one-hop policies. The full research cap-sweep runtime scaled by S1 density is used conservatively for cap policies. Country/target-mix drift and true V2 peak RSS remain unverified.')
    add('')
    rows = []
    for name in ('v1_plus_all',best_ex_name,*caps.keys()):
        z = r['policies'][name]
        rows.append((name,f(z['projected_test_candidates']),f(z['projected_candidate_parquet_bytes']/1e9,2),
          f(z['projected_feature_parquet_bytes']/1e9,2),f(z['projected_score_parquet_bytes']/1e9,2),
          f(z['projected_peak_temp_bytes']/1e9,2),f(z['projected_feature_runtime_seconds']/3600,2),
          f(z['projected_scoring_runtime_seconds']/3600,2),
          f(z['full_runtime_ratio_vs_measured_v1'],3),f(z['expected_peak_rss_bytes']/1e9,2),
          f(z['candidate_gate_le_400m']),f(z['runtime_gate_le_1p5x_v1'])))
    add(table(['Policy','Test pairs','Cand GB','Feature GB','Score GB','Temp GB',
      'Feature h','Scoring h','Full runtime × V1','Peak RSS proxy GB','≤400M','≤1.5×'],rows))
    add('')
    add('`v1_plus_all` full-runtime gate is unknown because its added pass-generation wall time was not measured here. Expansion index peak RSS and temporary disk are measured in the index receipts; reported full-test RSS remains a V1 per-stage proxy, not a verified V2 maximum. The paired resource gates are projections, not actual test runs.')
    add('')
    add('## 9. Reproducibility, tests, and access ledger')
    add('')
    add('All Phase 2 candidates were generated deterministically; per-part receipts and manifests record population, row/candidate counts, duplicate and invalid-ID counts, provenance, configuration, and SHA-256. The inventory covers the materialized candidates, features, scores, per-S1 metrics, preflight, projection, tests and the preserved plan. Key inputs and manifest hashes:')
    add('')
    keyfiles = ['step0_evaluation.json','neighbor_preflight.json','neighbor_index_df1000/manifest.json',
      'neighbor_index_df5000/manifest.json','expansion_grid/df1000_manifest.json',
      'expansion_grid/df5000_manifest.json','expansion_new_candidates/manifest.json',
      'expansion_new_features/manifest.json','expansion_new_scores/manifest.json',
      'expansion_evaluation.json','cap_sweep/manifest.json','cap_new_candidates/manifest.json',
      'cap_new_features/manifest.json','cap_new_scores/manifest.json','cap_evaluation.json',
      'resource_projection_with_generation.json','test_results_final.json','v1_integrity_check.json']
    add(table(['Artifact','SHA-256'],[(x,'`'+sha(OUT/x)+'`') for x in keyfiles]))
    add('')
    add(f'Tests: {f(t["v1"]["passed"])} V1 passed, {f(len(t["v1"]["skipped"]))} deliberately skipped, {f(t["v2"]["passed"])} V2 passed, zero failures/errors. Skipped V1 tests and reasons:')
    add('')
    for x in t['v1']['skipped']:
        add(f'- `{x["test"]}` — {x["reason"]}')
    add('')
    integrity = load('v1_integrity_check.json')
    add(f'Frozen V1 identifier check: **{integrity["status"]}**, four policy/feature/model/inference SHA-256 values match, `release_v1` tag matches the recorded commit, and tracked release source has {f(len(integrity["tracked_release_source_diff"]))} changed files. This check did not read test rows or V1 output files; the earlier full V1 immutability manifest is retained separately.')
    add('')
    add('**Files read (Phase 2):** frozen V1 candidate, rank, feature-spec, model and decision-policy artifacts; normalized `train_s1/s2/s3` keys and complete target name4 DF/postings; research membership; research GT/truth counts and research diagnostics after audits; historical aggregate V1 test receipts; original strategic plan and test source files. Exact materialized Phase 2 paths and hashes are in the inventory and manifests. **Files written:** only new `work/v2_phase2_r1` artifacts, the research branch Phase 2 scripts, the updated root strategic plan, and append-only V2 access-ledger events. No V1 artifact was modified.')
    add('')
    add('**Populations touched:** `v2_candidate_research` membership and labels for evaluation; complete inference-available training target corpus for indexing; sealed-population row-level membership IDs were decoded once by the first Phase 2 guard before it was replaced. **Labels accessed:** research labels only. **Sealed labels accessed:** zero. **Sealed populations accessed:** nonzero membership-only guard read, disclosed in `work/v2_access_ledger.jsonl` event `phase2_initial_guard_membership_decode`. **Real test data accessed:** zero rows; only historical aggregate stage receipts were read. No GT-derived seed, neighbor key/index, blocking key, quota, source direction, or parameter selection was used. GT was joined only after candidate artifacts were materialized, checksummed and structurally audited. The earlier guard incident prevents a claim of strict firewall compliance.')
    add('')
    add('## 10. Research recommendation and human gate')
    add('')
    gate_target = vx['macro_f05'] + .02
    best_measured = max(best_ex['primary_frozen_v1']['macro_f05'],best_cap['frozen_matcher']['macro_f05'])
    add(f'**MEASURED FACT:** The human gate requires macro F0.5 ≥ {f(gate_target)} (V1 research {f(vx["macro_f05"])} + 0.02). The best tested policy reaches {f(best_measured)}. Matcher-seeded one-hop expansion produced a positive paired gain, but its magnitude is well below +0.02; resource feasibility alone does not establish eligibility. The membership guard incident independently breaks the required zero sealed-population access condition. No agent gate decision is made.')
    add('')
    add('**DIAGNOSTIC CEILING:** The 37,866 retrieved-sister opportunities and candidate oracle scores indicate where recovery might exist; they are not operational gains. **RESEARCH-TUNED RESULT:** t2=0.30/0.45 variants use research labels for comparison and require independent future confirmation before use. **INFERENCE-SAFE RESULT:** The one-hop candidates and cap contrasts were produced from frozen scores and inference-available target evidence, then scored by frozen V1. **HYPOTHESIS:** New character-level, address-missing, cross-source and entity-level matcher features may improve discrimination on the still-unaccepted recovered GT links. **SPECULATIVE POSSIBILITY:** Other bounded retrieval or two-hop designs might improve coverage, but this phase did not measure them. No 0.99+ feasibility claim follows.')
    add('')
    add('The strongest evidence-backed next experiment is a human-approved, research-only matcher-feature ablation focused on the newly retrieved true links, address-missing truth pairs and script conflicts, with a prospectively frozen candidate policy and a renewed firewall review before any sealed population is opened. This recommendation ends the phase; no holdout, matcher training, threshold population, final evaluation, test inference or submission follows automatically.')
    add('')
    add('## Final six answers')
    add('')
    add(f'1. **What did V1 achieve on the research population?** Macro F0.5 {f(vx["macro_f05"])}, candidate pair recall {f(vx["candidate_pair_recall"])}, candidate oracle F0.5 {f(vx["candidate_oracle_macro_f05"])}.')
    add(f'2. **What did v1_plus_all achieve end-to-end?** Macro F0.5 {f(px["macro_f05"])}; Δ vs V1 {f(step0_b["point_delta"])} with a positive paired 95% CI.')
    eb = best_ex['primary_frozen_v1']['paired_bootstrap_vs_plus_all']
    add(f'3. **Did matcher-seeded collective expansion produce a statistically significant end-to-end improvement?** Yes. Best predeclared frozen result `{best_ex_name}`: Δ {f(eb["point_delta"])}, 95% CI [{f(eb["ci_95"][0])}, {f(eb["ci_95"][1])}]; the effect is small.')
    eligible_caps = [(name,x) for name,x in caps.items() if r['policies'][name]['candidate_gate_le_400m'] and r['policies'][name]['runtime_gate_le_1p5x_v1']]
    significant_caps = [(name,x) for name,x in eligible_caps if x['frozen_matcher']['paired_bootstrap_vs_plus_all']['ci_lower_bound'] > 0]
    if significant_caps:
        name,x = max(significant_caps,key=lambda z:z[1]['frozen_matcher']['macro_f05'])
        answer = f'Yes in the projected resource envelope: `{name}` Δ {f(x["frozen_matcher"]["paired_bootstrap_vs_plus_all"]["point_delta"])} with a positive CI lower bound; strict firewall eligibility still fails.'
    else:
        answer = 'No tested cap configuration had both a positive paired CI lower bound and projected candidate/runtime limits.'
    add(f'4. **Did cap/quota changes produce a statistically significant end-to-end improvement within full-test resource limits?** {answer}')
    add('5. **What is the strongest evidence-backed next experiment?** A human-approved research-only matcher-feature ablation on newly retrieved GT links and the address/script failure slices, after firewall review.')
    add('6. **Which populations remain sealed?** `v2_candidate_holdout`, `v2_matcher_train`, `v2_matcher_tune`, `v2_threshold`, and `v2_final_eval` remain label-sealed; their membership IDs were decoded once by the initial guard. Real test rows remain untouched.')
    (OUT / 'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(OUT/'report.md'),'report_sha256':sha(OUT/'report.md'),
      'inventory_sha256':inv_sha,'best_expansion':best_ex_name,'best_cap':best_cap_name},indent=2))


if __name__ == '__main__':
    run()
