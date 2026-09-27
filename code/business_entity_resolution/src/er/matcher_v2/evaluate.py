"""Full candidate-set metrics with retrieval misses and singleton S1s retained."""
import numpy as np
from .decision import entity_f05

def evaluate(entity,labels,scores,truth,active,threshold=.61,accepted=None,source=None,address=None,script=None,denominators=None):
    accepted=np.asarray(scores)>=threshold if accepted is None else np.asarray(accepted,bool)
    n=len(truth)
    pred=np.bincount(entity[accepted],minlength=n)
    tp=np.bincount(entity[accepted & (labels==1)],minlength=n)
    f=entity_f05(truth,pred,tp)
    t=truth[active];p=pred[active]
    singleton=t==0;multi=t>1
    result={'macro_f05':float(f[active].mean()),'s1':int(len(active)),
      'gt_links':int(t.sum()),'predicted':int(p.sum()),'true_positive':int(tp[active].sum()),
      'precision':float(tp.sum()/max(pred.sum(),1)), 'recall':float(tp.sum()/max(t.sum(),1)),
      'singleton_f05':float((p[singleton]==0).mean()) if singleton.any() else None,
      'empty_prediction_rate':float((p==0).mean()),'average_predictions_per_s1':float(p.mean()),
      'underprediction_rate':float((p<t).mean()),
      'overprediction_rate':float((p>t).mean()),
      'multimatch_underprediction_rate':float((p[multi]<t[multi]).mean()) if multi.any() else None,
      'multimatch_overprediction_rate':float((p[multi]>t[multi]).mean()) if multi.any() else None}
    recovered=np.bincount(entity[labels==1],minlength=n)
    result['candidate_pair_recall']=float(recovered.sum()/max(t.sum(),1))
    result['oracle_macro_f05']=float(entity_f05(truth,recovered,recovered)[active].mean())
    if result['macro_f05']>result['oracle_macro_f05']+1e-9:raise RuntimeError('Score exceeds candidate oracle')
    if denominators:
      for field,array in (('source',source),('address',address),('script',script)):
        result[field+'_slices']={}
        for value in (False,True):
            mask=np.asarray(array,bool)==value;sel=accepted&mask
            hits=int((sel & (labels==1)).sum());total=int(denominators[field][str(value)])
            result[field+'_slices'][str(value)]={'truth':total,'tp':hits,'predicted':int(sel.sum()),
                'precision':hits/max(int(sel.sum()),1),'recall':hits/max(total,1)}
    return result,f

def paired(delta,seed=20260927):
    rng=np.random.default_rng(seed)
    values=np.array([delta[rng.integers(0,len(delta),len(delta))].mean() for _ in range(1000)])
    return {'delta':float(delta.mean()),'ci95':np.quantile(values,[.025,.975]).tolist(),'unit':'S1','resamples':1000,'seed':seed}
