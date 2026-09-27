"""Label-free V2 feature adaptation for an arbitrary complete candidate set."""
from __future__ import annotations

from collections import Counter,defaultdict
from collections.abc import Mapping
import json

import numpy as np
import pyarrow as pa

from .data import V1,FEATURES,GROUPS
from .access import sha
from .features import (address_features,cross_features,name_features,
                       script_features,weighted_overlap)


def score_v1_table(v1_table,model_path,policy_path,expected_model_sha):
    """Score the frozen teacher using its pinned feature order and bytes."""
    import lightgbm as lgb
    if sha(model_path)!=expected_model_sha:raise PermissionError('Frozen teacher model changed')
    policy=json.loads(policy_path.read_text())
    order=policy['feature_order']
    if order!=V1:raise PermissionError('Frozen teacher feature order changed')
    missing=[name for name in order if name not in v1_table.column_names]
    if missing:raise ValueError('Missing teacher features: '+','.join(missing))
    model=lgb.Booster(model_file=str(model_path))
    if model.feature_name()!=order:raise PermissionError('Teacher model feature metadata differs')
    result=np.empty(len(v1_table),np.float32)
    for start in range(0,len(result),200000):
        batch=v1_table.slice(start,min(200000,len(result)-start))
        matrix=np.column_stack([np.asarray(batch[name],dtype=np.float32) for name in order])
        if not np.isfinite(matrix).all():raise ValueError('Nonfinite frozen teacher feature')
        result[start:start+len(batch)]=model.predict(matrix).astype(np.float32)
    return result


def _record(records: Mapping[str,Mapping[str,object]],entity_id: str):
    try:return records[entity_id]
    except KeyError:raise KeyError(f'Missing normalized record for {entity_id}') from None


def adapt_table(v1_table: pa.Table,source_records,target_records,weight_maps,
                teacher_scores) -> pa.Table:
    """Recompute all V2 additions from exactly the candidates in ``v1_table``.

    ``v1_table`` may contain extra columns, including diagnostics or labels; only
    the frozen V1 feature order and pair IDs are consumed. Context and anchors are
    computed over the entire supplied table, so callers must provide complete S1
    neighborhoods rather than independent fragments of the same S1.
    """
    required=['source1_entity_id','target_entity_id',*V1]
    missing=[name for name in required if name not in v1_table.column_names]
    if missing:raise ValueError('Missing V1 adapter columns: '+','.join(missing))
    n=len(v1_table);scores=np.asarray(teacher_scores,dtype=np.float32)
    if scores.shape!=(n,) or not np.isfinite(scores).all():
        raise ValueError('Teacher scores must be one finite value per candidate')
    s1=v1_table['source1_entity_id'].to_pylist();target=v1_table['target_entity_id'].to_pylist()
    pairs=list(zip(s1,target))
    if any(a is None or b is None for a,b in pairs) or len(set(pairs))!=n:
        raise ValueError('Candidate pair IDs must be nonnull and unique')
    if any(not (mid.startswith('S2-') or mid.startswith('S3-')) for mid in target):
        raise ValueError('Target IDs must use S2/S3 namespaces')
    source=[_record(source_records,x) for x in s1]
    targets=[_record(target_records,x) for x in target]
    for entity_id,record in [*zip(s1,source),*zip(target,targets)]:
        if any(key not in record for key in ('name_norm','addr_norm','country_norm')):
            raise KeyError(f'Incomplete normalized record for {entity_id}')
    s1_counts=Counter(s1);groups=defaultdict(list)
    for i,(sid,mid) in enumerate(pairs):groups[(sid,mid.startswith('S2-'))].append(i)
    context={name:np.zeros(n,np.float32) for name in GROUPS['entity']}
    anchors=defaultdict(list)
    for (sid,is_s2),indices in groups.items():
        ordered=sorted(indices,key=lambda i:(-float(scores[i]),target[i]))
        top=float(scores[ordered[0]]);second=float(scores[ordered[1]]) if len(ordered)>1 else 0.
        high=sum(float(scores[i])>=.61 for i in indices)
        for i in indices:
            context['v1_score'][i]=scores[i]
            context['candidate_count'][i]=s1_counts[sid]
            context['source_candidate_count'][i]=len(indices)
            context['top_score'][i]=top;context['second_score'][i]=second
            context['score_gap'][i]=top-second;context['high_count'][i]=high
            context['relative_to_top'][i]=float(scores[i])-top
        for rank,i in enumerate((i for i in ordered if float(scores[i])>=.61)):
            if rank==2:break
            anchors[sid].append({'mid':target[i],'name':targets[i]['name_norm'],
                'addr':targets[i]['addr_norm'],'score':float(scores[i]),'rank':rank})
    for values in anchors.values():values.sort(key=lambda x:(x['rank'],x['mid']))
    added={name:np.zeros(n,np.float32) for name in FEATURES if name not in V1}
    for i,(sid,mid) in enumerate(pairs):
        src=source[i];tgt=targets[i];country=str(src['country_norm'])
        if country not in weight_maps:raise KeyError(f'Missing complete-target DF weights for {country}')
        weights=weight_maps[country]
        if 'name' not in weights or 'addr' not in weights:
            raise KeyError(f'Incomplete complete-target DF weights for {country}')
        sn=str(src['name_norm']);sa=str(src['addr_norm']);tn=str(tgt['name_norm']);ta=str(tgt['addr_norm'])
        values={**name_features(sn,tn),**address_features(sa,ta),**script_features(sn,tn),
          **cross_features({'mid':mid,'name':tn,'addr':ta},anchors.get(sid,[])),
          'name_weighted_jaccard':weighted_overlap(sn,tn,weights['name']),
          'addr_weighted_jaccard':weighted_overlap(sa,ta,weights['addr'])}
        for name,value in values.items():added[name][i]=value
    columns={'source1_entity_id':pa.array(s1,type=pa.string()),
             'target_entity_id':pa.array(target,type=pa.string())}
    for name in V1:
        values=np.asarray(v1_table[name],dtype=np.float32)
        if values.shape!=(n,) or not np.isfinite(values).all():raise ValueError('Invalid V1 feature: '+name)
        columns[name]=pa.array(values,type=pa.float32())
    for name in FEATURES:
        if name in V1:continue
        values=context[name] if name in context else added[name]
        columns[name]=pa.array(values,type=pa.float32())
    return pa.table(columns)
