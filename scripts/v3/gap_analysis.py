"""Prepared current-union miss audit; never creates GT-conditioned candidates."""
import argparse
from common import ROOT,OUT,sha,write_new,execute_gate,research_connection,literal

def run():
    target=OUT/'gap_analysis.json'
    if target.exists():raise FileExistsError(target)
    con,count=research_connection('gap')
    candidates=ROOT/'work/v2_research/v1_plus_all.parquet'
    import json
    audit=json.loads((ROOT/'work/v2_research/v1_plus_all_structural_audit.json').read_text())
    if audit['status']!='PASS' or sha(candidates)!=audit['sha256']:raise PermissionError('Candidate audit changed')
    gt=ROOT/'work/v2_research/research_gt.parquet'
    receipt=json.loads((ROOT/'work/v2_research/research_gt_receipt.json').read_text())
    if sha(gt)!=receipt['sha256']:raise PermissionError('Research labels changed')
    diagnostics=ROOT/'work/v2_research/v1_link_diagnostics.parquet'
    con.execute(f"""CREATE TEMP TABLE misses AS SELECT d.*
      FROM read_parquet({literal(diagnostics)}) d JOIN authorized_development a USING(s1)
      ANTI JOIN read_parquet({literal(candidates)}) c
      ON d.s1=c.source1_entity_id AND d.mid=c.target_entity_id""")
    total,s1=con.sql('SELECT count(*),count(DISTINCT s1) FROM misses').fetchone()
    signals={
      'no_eligible_v1_pair_key':'NOT v1_key_eligible',
      'eligible_v1_pair_lost':'v1_key_eligible',
      'df_above_cutoff':'NOT v1_key_eligible AND (name_min_df>2000 OR addr_min_df>2000)',
      'name_variation':'NOT exact_name AND name_3gram_overlap>0',
      'address_variation':'both_address AND sa<>ta AND address_3gram_overlap>0',
      'acronym_or_initial_evidence':'acronym_equal OR initials_equal',
      'script_mismatch':'NOT same_script',
      'devanagari_latin':'devanagari_latin',
      'numeric_evidence':'numeric_overlap>0 OR house_equal OR postal_equal',
      'cross_source_sister_evidence':'cross_source_sister',
      'format_reordering_evidence':'name_token_sort_ratio>name_ratio+0.15',
    }
    rows=[]
    for name,condition in signals.items():
        pairs,entities=con.sql(f'SELECT count(*),count(DISTINCT s1) FROM misses WHERE {condition}').fetchone()
        rows.append({'category':name,'missed_gt_pairs':pairs,'s1':entities,'fraction_of_misses':pairs/max(total,1),
          'recoverability':'diagnostic opportunity only; must measure label-free pass','candidate_cost':None})
    groups={}
    for field in ('country','target_source','both_address','script_relation'):
        groups[field]=[dict(zip((field,'pairs','s1'),r)) for r in con.sql(
          f'SELECT {field},count(*),count(DISTINCT s1) FROM misses GROUP BY 1 ORDER BY 1').fetchall()]
    write_new(target,{'status':'DIAGNOSTIC_ONLY','development_s1':count,'missed_pairs':total,'missed_s1':s1,
      'categories':rows,'slices':groups,'candidate_sha256':sha(candidates),'research_gt_sha256':sha(gt),
      'diagnostic_sha256':sha(diagnostics),'categories_overlap':True,
      'unverified_categories':['DBA/trade name: needs observable marker analysis','causal abbreviation loss: not established by initials alone'],
      'historical_count_correction':'19897 referred to V1 missed pairs, not distinct no-key S1; current union and development subset differ',
      'inference_restriction':'Never use these labels or missed-pair memberships to select candidate-generator queries',
      'sister_limitation':'Historical sister flags refer to V1 recovered truth; not recomputed using the current union',
      'assessment_labels_read':False,'sealed_membership_read':False})
    con.close();print(target,flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true')
    args=parser.parse_args();execute_gate(args);run()
