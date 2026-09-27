"""Select only research-supported retrieval proposals for label-free DF preflight."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.runtime import R,sha,write_json,log,populations


def run():
    research,sealed=populations()
    misses=json.loads((R/'v1_miss_decomposition.json').read_text())['missing_links']
    signals=json.loads((R/'v1_opportunity.json').read_text())
    entities=json.loads((R/'v1_entity_set_analysis.json').read_text())
    if misses<=0: raise RuntimeError('No missed links; no retrieval proposal justified')
    def count(name): return signals[name]['missed_links_with_signal']
    proposals={}
    for n in (2,3,4):
        x=count(f'name_{n}gram_overlap')
        proposals[f'name_char_{n}gram']={'observed_missed_links_with_signal':x,'denominator':misses,
            'fraction':x/misses,'decision':'PROFILE_FULL_TARGET_DF' if x>=100 and x/misses>=.01 else 'REJECT_LOW_OPPORTUNITY',
            'note':'Overlap is a broad upper bound, not incremental recoverability.'}
    x=count('address_4gram_overlap')
    proposals['address_character_ngrams']={'observed_missed_links_with_signal':x,'denominator':misses,
        'fraction':x/misses,'decision':'PROFILE_FULL_TARGET_DF' if x>=100 and x/misses>=.01 else 'REJECT_LOW_OPPORTUNITY'}
    x=count('acronym_equal')
    proposals['acronym_initials']={'observed_missed_links_with_signal':x,'denominator':misses,
        'fraction':x/misses,'decision':'PROFILE_FULL_TARGET_DF' if x>=100 and x/misses>=.005 else 'REJECT_LOW_OPPORTUNITY',
        'safeguards':'length>=3, country partition, common acronym DF cap, address/numeric corroboration for ambiguous keys'}
    x=count('postal_equal')+count('house_equal')
    proposals['structured_address']={'postal_plus_house_signal_occurrences':x,'denominator':misses,
        'decision':'PROFILE_FULL_TARGET_DF' if x>=100 else 'REJECT_LOW_OPPORTUNITY',
        'note':'Signals overlap; this sum is not a distinct-link count.'}
    x=count('devanagari_latin')
    proposals['transliteration']={'observed_cross_script_devanagari_latin_misses':x,'denominator':misses,
        'decision':'PROFILE_AND_LICENSE_REVIEW' if x>=500 and x/misses>=.01 else 'REJECT_LOW_OPPORTUNITY',
        'note':'Additional representation only; no external identity data.'}
    x=entities['missed_links_sister_closer_than_s1']
    proposals['one_stage_sister_expansion']={'observed_missed_links_closer_to_retrieved_true_target':x,
        'denominator':misses,'decision':'PROFILE_INFERENCE_SEED_CARDINALITY' if x>=500 and x/misses>=.01 else 'REJECT_LOW_OPPORTUNITY',
        'caveat':'The diagnostic used true sisters; an implemented seed must be selected by inference evidence only.'}
    result={'status':'RESEARCH_DIAGNOSTIC_PROPOSALS','research_s1':len(research),
            'missed_links':misses,'proposals':proposals,
            'next_gate':'DF/cardinality/resource preflight before any implemented retrieval join',
            'diagnostic_only_label_access':True,
            'opportunity_sha256':sha(R/'v1_opportunity.json'),
            'entity_set_sha256':sha(R/'v1_entity_set_analysis.json')}
    write_json(R/'pass_opportunity_gate.json',result)
    log('pass_opportunity_gate',command='.venv\\Scripts\\python.exe -B scripts\\v2_opportunity_gate.py',
        v2_research_label_read=True,scope='research-only diagnostic summaries',manifest_sha256=sha(R/'pass_opportunity_gate.json'))
    print(json.dumps(result,indent=2))


if __name__=='__main__': run()
