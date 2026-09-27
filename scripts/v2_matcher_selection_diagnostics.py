"""Development-only ranking/threshold ceilings; never used as inference rules."""
from pathlib import Path
import sys,json
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.matcher_v2.data import OUT,write_once
from er.matcher_v2.experiments import role_data,validate_selected_artifacts,metrics
from er.matcher_v2.decision import entity_f05

if (OUT/'assessment.json').exists():raise PermissionError('No adaptive diagnosis after assessment')
selected=validate_selected_artifacts();d=role_data(1)
scores=np.load(OUT/'models'/selected['experiment_id']/'selection_scores.npy')
rows=[];positive=d['y']==1
for threshold in np.r_[np.arange(.1,.91,.05),.925,.95,.975]:
    accepted=scores>=threshold
    pn=np.bincount(d['entity'][accepted],minlength=100000)
    tp=np.bincount(d['entity'][accepted&positive],minlength=100000)
    value=float(entity_f05(d['truth'],pn,tp)[d['active']].mean())
    rows.append({'threshold':float(threshold),'macro_f05':value})
oracle=np.where(d['truth']==0,1.,0.);borders=np.r_[0,np.flatnonzero(np.diff(d['entity']))+1,len(scores)]
for left,right in zip(borders[:-1],borders[1:]):
    idx=int(d['entity'][left]);order=np.argsort(-scores[left:right],kind='stable')
    p=scores[left:right][order];tp=np.cumsum(d['y'][left:right][order]);k=np.arange(1,len(p)+1)
    f=1.25*tp/(.25*d['truth'][idx]+k)
    # Equal scores cannot be split by a score threshold.
    ends=np.r_[p[:-1]!=p[1:],True]
    oracle[idx]=max(oracle[idx],float(f[ends].max(initial=0)))
best=max(rows,key=lambda r:r['macro_f05']);m,_=metrics(d,scores,threshold=best['threshold'])
result={'model':selected['experiment_id'],'role':'model_select only; previously used development cohort',
 'global_threshold_diagnostic':rows,'best_global_selection':m,
 'oracle_per_entity_score_threshold':float(oracle[d['active']].mean()),
 'interpretation':'Per-entity threshold oracle uses labels only as an optimistic diagnostic; it is not a deployable policy. Candidate oracle is a separate ceiling. No assessment or policy labels used.'}
write_once(OUT/'selection_ranking_diagnostics.json',result)
print(json.dumps({'best_global':best,'per_entity_threshold_oracle':result['oracle_per_entity_score_threshold'],
 'candidate_oracle':m['oracle_macro_f05']}),flush=True)
