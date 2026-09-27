"""Render the amended V2 research evidence and materialized Pareto frontier."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
W=ROOT/'work';R=W/'v2_research'


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def value(x):return f'{x:,}' if isinstance(x,int) else str(x)
def pct(x):return f'{100*x:.3f}%'
def mb(n):return f'{n/1_000_000:.1f} MB'


def main():
    preservation=read(W/'v2_v1_immutability_manifest.json')
    reclass=read(W/'v2_historical_touch_reclassification.json')
    split=read(W/'v2_split_checksums.json')
    baseline=read(R/'v1_metrics.json')
    base_manifest=read(R/'v1_baseline_manifest.json')
    miss=read(R/'v1_miss_decomposition.json')
    opp=read(R/'v1_opportunity.json')
    entity=read(R/'v1_entity_set_analysis.json')
    ngram=read(R/'ngram_preflight.json')
    other=read(R/'other_preflight.json')
    house=read(R/'short_house_preflight.json')
    sister=read(R/'sister_preflight.json')
    key_pre=read(R/'sister_seed_key_preflight.json')
    cap_diag=read(R/'pass_cap_diagnostics.json')
    frontier=read(R/'policy_evaluation.json')
    tests=read(W/'v2_test_results.json')
    access=read(R/'access_audit.json')
    ledger=[json.loads(line) for line in (W/'v2_access_ledger.jsonl').read_text(encoding='utf-8').splitlines()]
    if preservation['status']!='PASS' or reclass['reclassified_source_count']!=598:
        raise RuntimeError('Preservation or historical reclassification incomplete')
    if split['status']!='ALLOCATED_PROSPECTIVE_SEAL' or access['status']!='PASS':
        raise RuntimeError('Prospective seal or access audit incomplete')
    if tests['exit_code'] or frontier['status']!='MATERIALIZED_RESEARCH_FRONTIER':
        raise RuntimeError('Tests or materialized frontier incomplete')
    if any(x.get('sealed_label_read') for x in ledger):
        raise PermissionError('Sealed label access recorded')
    diff=subprocess.run(['git','diff','--quiet','release_v1','--','code/business_entity_resolution','output','broCode_submission.zip'],cwd=ROOT).returncode
    if diff:raise RuntimeError('Tracked V1 production/release path changed')
    readme_branch_diff=subprocess.run(['git','diff','--quiet','release_v1','--','README.md'],cwd=ROOT).returncode!=0
    readme_worktree_clean=subprocess.run(['git','diff','--quiet','HEAD','--','README.md'],cwd=ROOT).returncode==0
    if not readme_worktree_clean:raise RuntimeError('Root README working tree changed during research')
    changed=subprocess.check_output(['git','status','--short','--untracked-files=all'],cwd=ROOT,text=True)
    policies=frontier['policies']
    names=list(policies)
    pass_names=[x for x in ('name_char4','acronym','postal_like_numeric','address_char4','sister_expansion') if (R/f'{x}_manifest.json').exists()]
    resources={name:read(R/f'{name}_resources.json') for name in pass_names}
    evaluator_resources=read(R/'policy_evaluation_resources.json')
    base_rss=read(R/'baseline_external_rss.json')
    base_tmp=read(R/'baseline_external_temp.json')
    base_rss_value=base_rss.get('sampled_peak_process_tree_rss_bytes',base_rss.get('peak_bytes'))
    base_tmp_value=base_tmp.get('sampled_peak_temp_disk_bytes',base_tmp.get('peak_bytes'))
    if base_rss_value is None or base_tmp_value is None:
        raise RuntimeError('External baseline resource monitor schema unrecognized')

    def cost(name,x):
        if name=='v1_baseline':
            return base_manifest['wall_seconds'],base_rss_value,base_tmp_value,x['materialized_artifact']['bytes']
        if name.endswith('_standalone'):
            p=name[:-len('_standalone')];r=resources[p]
            return r['wall_seconds'],r['sampled_peak_process_tree_rss_bytes'],r['sampled_peak_temp_disk_bytes'],x['materialized_artifact']['bytes']
        if name.startswith('v1_plus_'):
            p=name[len('v1_plus_'):]
            parts=pass_names if p=='all' else [p]
            runtime=base_manifest['wall_seconds']+sum(resources[z]['wall_seconds'] for z in parts)+x.get('union_materialization_wall_seconds',0)
            rss=max([base_rss_value,evaluator_resources['sampled_peak_process_tree_rss_bytes']]+[resources[z]['sampled_peak_process_tree_rss_bytes'] for z in parts])
            temp=max([base_tmp_value,evaluator_resources['sampled_peak_temp_disk_bytes']]+[resources[z]['sampled_peak_temp_disk_bytes'] for z in parts])
            return runtime,rss,temp,x['materialized_artifact']['bytes']
        raise RuntimeError(f'Unknown policy: {name}')

    table=['| Policy | Candidates | Mean/S1 | P95 | P99 | Max | Pair recall | Oracle F0.5 | GT added | GT lost | GT/1k added | RSS | Temp disk | Artifact | Runtime | Frontier |',
      '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for name in names:
        x=policies[name];d=x['comparisons_vs_v1'];seconds,rss,tmp,disk=cost(name,x)
        efficiency=d['gt_recovered_per_1000_added']
        table.append(f"| `{name}` | {value(x['candidate_count'])} | {x['mean_candidates_per_s1']:.2f} | {x['p95']:.0f} | {x['p99']:.0f} | {x['max']} | {pct(x['pair_candidate_recall'])} | {x['oracle_macro_f05']:.6f} | {value(d['gt_added'])} | {value(d['gt_lost'])} | {efficiency:.2f} | {mb(rss)} | {mb(tmp)} | {mb(disk)} | {seconds:.0f}s | {'yes' if name in frontier['nondominated'] else 'no'} |" if efficiency is not None else
          f"| `{name}` | {value(x['candidate_count'])} | {x['mean_candidates_per_s1']:.2f} | {x['p95']:.0f} | {x['p99']:.0f} | {x['max']} | {pct(x['pair_candidate_recall'])} | {x['oracle_macro_f05']:.6f} | {value(d['gt_added'])} | {value(d['gt_lost'])} | n/a | {mb(rss)} | {mb(tmp)} | {mb(disk)} | {seconds:.0f}s | {'yes' if name in frontier['nondominated'] else 'no'} |")
    (R/'policy_frontier.md').write_text('# Materialized V2 research Pareto frontier\n\n'+'\n'.join(table)+
      '\n\nRuntime for unions is measured baseline retrieval plus measured pass materialization plus measured union creation; it excludes offline DF preflight and final metric calculation. RSS and temporary disk for unions are conservative maxima across retrieval and the shared evaluator process. Artifact size is the final candidate file size; input/intermediate storage is additional. No policy is holdout validated.\n',encoding='utf-8')

    best=max(((n,x) for n,x in policies.items() if n.startswith('v1_plus_') or n=='v1_baseline'),
             key=lambda pair:(pair[1]['pair_candidate_recall'],pair[1]['oracle_macro_f05']))
    target_met=[n for n,x in policies.items() if x['pair_candidate_recall']>=.97 and
                x['oracle_macro_f05']>=.985 and x['mean_candidates_per_s1']<=250]
    lines=['# V2 candidate-generation research — human-approved eligibility amendment','',
      '**Status: RESEARCH RECOMMENDATION. A VALIDATED V2 POLICY does not exist.**','',
      '## Executive finding','',
      f"The previous `BLOCKED_INSUFFICIENT_POOL` stop was correct under the original rule. The approved exposure amendment made {value(reclass['eligible_s1'])} old-model-fit S1s eligible. The requested split was allocated without resizing. Only `v2_candidate_research` labels were used for this report. All protected populations are **prospectively sealed v2 populations**; their historical aggregate-only exposure is disclosed below.",'',
      f"The frozen V1 baseline recovered {value(baseline['recovered_links'])}/{value(baseline['gt_links'])} research GT links ({pct(baseline['pair_recall'])}), with oracle macro F0.5 {baseline['oracle_macro_f05']:.6f}. The highest-recall materialized union tested here, `{best[0]}`, reached {pct(best[1]['pair_candidate_recall'])} and oracle {best[1]['oracle_macro_f05']:.6f}. The research targets were pair recall 97%, oracle 0.985, and mean candidates at most 250. {'At least one materialized point met all three targets.' if target_met else 'No materialized point met all three targets.'}",'',
      '## V1 preservation','',
      f"Tag `{preservation['release_tag']}` at `{preservation['release_commit']}`; branch `{preservation['research_branch']}`. All {len(preservation['artifacts'])} frozen artifacts were checksum verified. Tracked V1 production/release paths are identical to `release_v1`. {'The root README differs from that tag at the pre-existing branch commit `5f4f328` (`Update README file`); it has no working-tree change from this research.' if readme_branch_diff else 'The root README also matches the tag.'} A source snapshot and read-only archive backup are recorded in `work/v2_v1_immutability_manifest.json` and `work/v2_v1_immutability_report.md`.",'',
      '| Artifact | SHA-256 |','|---|---|']
    for path in ('broCode_submission.zip','output/candidate_pairs.tsv','output/matching_results.tsv',
                 'code/business_entity_resolution/release/final_candidate_policy.json',
                 'code/business_entity_resolution/release/feature_spec_v1_1.json',
                 'code/business_entity_resolution/release/final_matcher_model.txt',
                 'code/business_entity_resolution/release/final_matcher_policy.json'):
        lines.append(f"| `{path}` | `{preservation['artifacts'][path]['actual_sha256']}` |")
    lines += ['', 'The v1 public score 0.876359 and visible first score 0.991811 are context only; neither selected a retrieval parameter.','',
      '## Historical exposure reclassification and eligibility','',
      'The original 598-source registry and old stop report remain unchanged. The amended Parquet registry contains one classification and all required evidence fields for every original source. One separately labelled D supplement covers five first-row profiler examples whose historical display could not be reconstructed.','',
      '| Category | Original sources | Distinct S1 | Old model_fit S1 | Exclusion |','|---|---:|---:|---:|---|']
    for category in 'ABCD':
        z=reclass['category_counts'][category]
        lines.append(f"| {category}: {z['name']} | {z['source_count']} | {value(z['distinct_s1'])} | {value(z['old_model_fit_s1'])} | {'yes' if category in 'CD' else 'no'} |")
    lines += ['',f"C/D union: {value(reclass['decision_relevant_touched_s1'])} S1 (`{reclass['decision_relevant_touched_id_sha256']}`); of these, {value(reclass['touched_model_fit_s1'])} were in old model_fit. Eligible old model_fit: {value(reclass['old_model_fit_s1'])} − {value(reclass['touched_model_fit_s1'])} = **{value(reclass['eligible_s1'])}** (`{reclass['eligible_id_sha256']}`). Category overlaps are explicitly recorded in the reclassification JSON. The original three corrupt source shards used the smallest proven population superset and remain D.",'',
      'The executed broad matcher audit was checked against its code and output: it emitted only population link counts, singleton/mean/histogram and integrity summaries. Its full-split scan is B, aggregate only; no per-S1 label, candidate outcome, or example from that audit fed V1 decisions. Other downstream row-level uses were separately classified C. See `work/v2_aggregate_exposure_verification.md`.','',
      '## Deterministic prospective allocation and seal','',
      f"Assignment order: `{split['ordering']}` with seed `{split['seed']}`. Manifest SHA-256 `{split['manifest']['sha256']}`. Zero population overlap; zero C/D overlap. The population ID hashes below are SHA-256 of sorted IDs with LF separators. Historical aggregate-only exposure is disclosed; sealed labels, outcomes, and metrics were not previously decision-relevant and have remained closed from allocation forward.",'',
      '| Population | S1 | Logical ID SHA-256 | Label state |','|---|---:|---|---|']
    for name,z in split['populations'].items():
        lines.append(f"| `{name}` | {value(z['s1'])} | `{z['id_sha256']}` | {'research only opened' if name=='v2_candidate_research' else 'prospectively sealed; unopened'} |")
    lines += ['', 'The prior global target-uniqueness invariant is supported by `work/integrity_report.md` (SHA-256 `'+split['target_uniqueness']['sha256']+'`). Direct overlap of labelled sealed targets is deferred, because proving it now would open protected labels.','',
      '## Frozen V1 baseline and miss forensics','',
      f"Complete training target corpus: {value(base_manifest['inputs']['train_s2']['rows'])} S2 + {value(base_manifest['inputs']['train_s3']['rows'])} S3 = {value(base_manifest['inputs']['train_s2']['rows']+base_manifest['inputs']['train_s3']['rows'])}. Full target files, V1 DF tables and policy are checksummed in `v1_baseline_manifest.json`. Candidate generation was label free; its {value(base_manifest['candidates'])} candidates were checksummed and structurally audited before the research-only GT join. No GT-filtered target index was used.",'',
      f"100,000 research S1; mean {baseline['mean']:.2f}, median {baseline['median']:.0f}, P90 {baseline['p90']:.0f}, P95 {baseline['p95']:.0f}, P99 {baseline['p99']:.0f}, max {baseline['max']}. All links recovered for {value(baseline['all_link_recovery_s1'])} nonempty S1, some for {value(baseline['some_link_recovery_s1'])}, none for {value(baseline['no_link_recovery_s1'])}; {value(baseline['singleton_s1'])} have zero GT links. Baseline wall {base_manifest['wall_seconds']:.1f}s, sampled process-tree RSS {mb(base_rss_value)}, sampled temporary disk {mb(base_tmp_value)}, artifact {mb(base_manifest['artifact_bytes'])}.",'',
      f"Exactly {value(miss['missing_links'])} GT links were missed: {value(miss['categories']['no_v1_retrieval_key_eligible'])} had no eligible V1 key, {value(miss['categories']['eligible_but_lost_through_ranking_cap'])} were lost after eligibility by shortlist/rank/cap, and {value(miss['categories']['other_verified_v1_retrieval_policy_loss'])} were other verified policy losses. These categories are mutually exclusive and exhaustive.",'',
      '| Non-exclusive diagnostic signal among missed links | Count |','|---|---:|']
    for key in ('exact_name','exact_nosuffix','exact_sorted','name_token_overlap','name_2gram_overlap','name_3gram_overlap','name_4gram_overlap',
                'address_token_overlap','address_4gram_overlap','acronym_equal','initials_equal','numeric_overlap','postal_equal','house_equal',
                'devanagari_latin','true_sister_retrieved','sister_closer_than_s1','cross_source_sister'):
        lines.append(f"| `{key}` | {value(opp[key]['missed_links_with_signal'])} / {value(miss['missing_links'])} |")
    lines += ['', 'The detailed `v1_link_diagnostics.parquet`, `v1_metrics.json`, `v1_opportunity.json`, and `v1_entity_set_analysis.json` contain country, target-source, address-presence, script, link-count, RapidFuzz and rank/DF breakdowns. Signals above overlap and are not summed.','',
      '## Entity-set and source bridge evidence','',
      f"Among {value(entity['nonempty_entity_sets'])} nonempty research sets, {value(entity['cross_source_entity_sets'])} contain both S2 and S3. Intra-set mean name similarity is {entity['intra_set_mean_target_name_similarity']:.3f} across {value(entity['intra_set_target_name_pairs'])} target pairs; mean address similarity is {entity['intra_set_mean_target_address_similarity']:.3f} across {value(entity['intra_set_target_address_pairs'])} pairs. Mean strongest S1→target name evidence is {entity['mean_strongest_s1_to_target_name_evidence']:.3f}; target→target is {entity['mean_strongest_target_to_target_name_evidence_per_set']:.3f}. {value(entity['missed_links_sister_closer_than_s1'])} misses are closer to a retrieved true sister than S1. These are diagnostic label measurements, never inference seed rules.",'',
      '## Pass preflights and decisions','',
      'All DF profiles use the complete permitted S2/S3 corpus, country partitions, explicit text-length filters, and one research process at a time. The counts below are join rows before pair deduplication and source quotas. Common keys are rejected by the cap.','',
      '| Proposed method | Country DF cap | Estimated joins | Worst key | Decision |','|---|---:|---:|---:|---|']
    for item in ngram['results']:
        cap=1000;z=next(x for x in item['bounded_options'] if x['df_cap']==cap)
        decision='materialized as `name_char4`' if item['n']==4 else 'profiled; no pass at this bound'
        lines.append(f"| name {item['n']}-gram, 4 rare keys | {cap} | {value(z['estimated_join_rows'])} | {value(z['worst_key_join_rows'])} | {decision} |")
    for kind,z in other['cases'].items():
        p=next(x for x in z['options'] if x['df_cap']==200)
        lines.append(f"| `{kind}` | 200 | {value(p['estimated_join_rows'])} | {value(p['worst_key_join_rows'])} | materialized and audited |")
    short=next(x for x in house['options'] if x['df_cap']==200)
    lines.append(f"| short house number alone, one key/S1 | 200 | {value(short['estimated_join_rows'])} | {value(short['worst_key_join_rows'])} | rejected as a standalone key; {value(short['source_s1_with_key'])} research S1 have a bounded key |")
    lines += [f"| one-seed opposite-source sister name 4-gram | 1,000 | {value(key_pre['estimated_same_country_all_source_join_rows_upper_bound'])} upper bound | {value(key_pre['worst_key_join_rows'])} | materialized and audited |",'',
      'At DF cap 1,000, name 2-grams selected only 1,113 source-key rows and trigrams 22,953, versus 121,348 for four-grams. The very common 2/3-gram keys were rejected by frequency. Those lengths were independently profiled but not presented as materialized operating points.','',
      f"Sister expansion has a diagnostic upper bound of {value(opp['true_sister_retrieved']['missed_links_with_signal'])} misses with some retrieved true sister, but the inference-available strict global top-one seed contains only {value(sister['strict_global_seed_quota_tiers'][0]['missed_links_with_opposite_source_true_seed'])} opposite-source true-sister opportunities. It was tested with exactly one inference-selected seed, one opposite-source stage, four rare name grams, and a ten-candidate expansion quota. No GT-derived seed selection or iterative propagation was used.",'',
      f"Devanagari/Latin relation occurs in {value(opp['devanagari_latin']['missed_links_with_signal'])} missed links ({pct(opp['devanagari_latin']['missed_links_with_signal']/baseline['gt_links'])} of all research GT links as a generous maximum). Transliteration was not materialized in this phase: full-target transformed-name DF and a reproducible permissive implementation were not established within the bounded experiments. This is a deferred hypothesis, not evidence that transliteration has no benefit.",'',
      '## Materialized standalone and incremental results','',
      'Candidate identity is `(source1_entity_id, target_entity_id)`. Each pass is switchable and has an independent checksummed policy config, intermediate receipts, country and shard bounds, quota, provenance bit, and structural audit. Combination provenance is stored separately as a mask. Every union was generated, checksummed and structurally audited before the final research-label metrics run.','',
      *table,'',
      '### Exact pass eligibility and quota losses','',
      '| Pass | GT links with an eligible pass key | Standalone recovered GT | GT lost at pass ranking/quota | Eligible V1 misses | New GT after quota | Incremental quota loss |',
      '|---|---:|---:|---:|---:|---:|---:|']
    for kind,z in cap_diag['passes'].items():
        lines.append(f"| `{kind}` | {value(z['eligible_gt_links_before_quota'])} | {value(z['recovered_gt_links_after_quota'])} | {value(z['gt_links_lost_to_ranking_quota'])} | {value(z['eligible_v1_missed_gt_links_before_quota'])} | {value(z['new_gt_links_after_quota'])} | {value(z['incremental_gt_lost_to_ranking_quota'])} |")
    lines += ['',
      'The table gives exact added and lost GT counts versus frozen V1. The source and slice metrics for every point are in `policy_evaluation.json`; candidate and GT arithmetic is materialized, not key-eligibility oracle projection. Stage-specific temporary disk and RSS are in `*_resources.json`. The frontier compares recall, oracle, mean candidate count and P99; it does not imply holdout validation.','',
      '## Resource projection and recommendation','']
    for name in ('v1_baseline',best[0]):
        x=policies[name]
        lines.append(f"- `{name}` at research density projects {value(round(x['mean_candidates_per_s1']*split['eligible_s1']))} candidates for {value(split['eligible_s1'])} eligible train S1, {value(round(x['mean_candidates_per_s1']*base_manifest['inputs']['train_s1']['rows']))} for all {value(base_manifest['inputs']['train_s1']['rows'])} training S1, or {value(round(x['mean_candidates_per_s1']*1_000_000))} per hypothetical million test S1. These are linear volume projections only; no real test records were accessed and test population size is not asserted.")
    lines += ['', 'Large-scale risk: DF and candidate volume change with target-corpus composition; country and worst-key filters must be reprofiled before any full-scale run. The observed baseline process-tree RSS was larger than DuckDB’s 700 MB internal cap. Intermediate postings and union files require additional disk beyond the final artifact. A single small research shard is not a proof of full-scale runtime.','',
      f"**RESEARCH RECOMMENDATION:** {'The materialized frontier contains a policy meeting all three quality/volume targets; send it to independent audit and human policy selection before any holdout opening.' if target_met else 'Do not freeze a V2 policy or open candidate holdout from these results. The best materialized recall point remains below the 97% pair-recall and 0.985 oracle targets. Prioritize a separate bounded investigation of the 22,059 V1 shortlist/rank/cap losses and inference-safe source bridging before a one-time holdout evaluation.'}",'',
      '## Tests and prospective access audit','',
      f"Command: `{tests['command']}`; exit {tests['exit_code']}. V1 discovered {tests['v1']['discovered']}, executed {tests['v1']['executed']}, passed {tests['v1']['passed']}, failed {tests['v1']['failed']}, errors {tests['v1']['errors']}, explicit skips {len(tests['v1']['skipped'])}. V2 executed {tests['v2']['executed']}, passed {tests['v2']['passed']}, failed {tests['v2']['failed']}, errors {tests['v2']['errors']}. **The complete V1 suite was not executed.** Four unchanged tests were skipped because they would decode real row-level labels outside this research scope.",'',
      'Skipped V1 assertions:']
    for x in tests['v1']['skipped']:
        lines.append(f"- `{x['test']}` — {x['reason']}")
    lines += ['',f"Access ledger: {len(ledger)} command/phase events, including {access['research_label_events']} research-label events and zero sealed-label events. Artifact audit reviewed {len(access['reviewed_artifacts'])} research or combined files against the exact 100,000-S1 research manifest and found no outside or sealed S1. The ledger is a command-level record, not an OS trace.",'',
      'The raw GT TSV was streamed to project research IDs; sealed rows were passed by binary ID comparison without decoding target labels, retaining them, or computing sealed metrics. This streaming limitation is disclosed in the access receipt. Research-only label reads are recorded by exact commands in `work/v2_access_ledger.jsonl`. The broad historical B scans occurred before the prospective seal and are disclosed in the reclassification and split reports.','',
      'Process-order disclosure: an exploratory `v2_evaluate.py` run measured research-only union metrics after union materialization and constituent-pass audits, but before writing checksums for the union artifacts themselves. No sealed labels were involved. The evaluator was amended to checksum and structurally audit every union before metrics; the final reported frontier was rerun through that stricter gate. Both runs and the correction are retained in the command-level ledger.','',
      '## Explicit access and release confirmations','',
      '- V1 release source, ZIP, candidate output and matching output remained unchanged. No V1 production candidate module was edited.',
      '- `v2_candidate_holdout`, `v2_matcher_train`, `v2_matcher_tune`, `v2_threshold` and `v2_final_eval` labels, outcomes and metrics remained unopened in this task.',
      '- Candidate retrieval used the complete permitted training S2/S3 target corpus. No GT-filtered target corpus or positive-neighborhood index was used.',
      '- No V2 matcher was trained; no matcher threshold was tuned.',
      '- No real test data or test labels were accessed, no full V2 test candidates or predictions were generated, and no leaderboard submission was made.','',
      '## Reproduction and changed files','',
      'Commands, in order after the preserved original registry:','',
      '```powershell',
      '.venv\\Scripts\\python.exe -B scripts\\v2_preserve.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_reclassify.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_allocate.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_baseline.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_baseline_audit.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_diagnose.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_ngram_preflight.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_other_preflight.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_house_preflight.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_name4_materialize.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_name4_audit.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_exactkey_materialize.py --pass acronym',
      '.venv\\Scripts\\python.exe -B scripts\\v2_exactkey_audit.py --pass acronym',
      '.venv\\Scripts\\python.exe -B scripts\\v2_exactkey_materialize.py --pass postal_like_numeric',
      '.venv\\Scripts\\python.exe -B scripts\\v2_exactkey_audit.py --pass postal_like_numeric',
      '.venv\\Scripts\\python.exe -B scripts\\v2_address4_materialize.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_exactkey_audit.py --pass address_char4',
      '.venv\\Scripts\\python.exe -B scripts\\v2_sister_preflight.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_sister_seed_preflight.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_sister_materialize.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_exactkey_audit.py --pass sister_expansion',
      '.venv\\Scripts\\python.exe -B scripts\\v2_pass_cap_diagnostics.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_evaluate.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_artifact_access_audit.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_verify.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_preserve.py',
      '.venv\\Scripts\\python.exe -B scripts\\v2_final_report.py',
      '```','',
      'Changed/new files in the workspace (ignored large Parquet shards may not appear here):','',
      '```text',changed.rstrip(),'```','',
      '**Final classification: RESEARCH RECOMMENDATION. No VALIDATED V2 POLICY exists.**','']
    out=W/'v2_research_recommendation.md';out.write_text('\n'.join(lines),encoding='utf-8')
    print(out)


if __name__=='__main__':main()
