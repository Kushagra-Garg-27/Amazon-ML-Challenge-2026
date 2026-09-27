"""Label-free current-model context and a bounded residual score corrector."""
from pathlib import Path
import numpy as np
import lightgbm as lgb
from .data import FEATURES

EXTRA=['current_score','current_top','current_second','current_gap','current_relative',
 'current_sum','current_high_count','current_source_top','current_source_relative']

def logits(p):
    p=np.clip(p,1e-6,1-1e-6)
    return np.log(p/(1-p))

def context(d,p):
    n=len(p);result=np.zeros((n,len(EXTRA)),np.float32);result[:,0]=p
    borders=np.r_[0,np.flatnonzero(np.diff(d['entity']))+1,n]
    for left,right in zip(borders[:-1],borders[1:]):
        q=p[left:right];src=d['source'][left:right]
        if not len(q):continue
        ordered=np.sort(q);top=ordered[-1];second=ordered[-2] if len(q)>1 else 0
        result[left:right,1:4]=[top,second,top-second]
        result[left:right,4]=q-top;result[left:right,5]=q.sum();result[left:right,6]=(q>=.7).sum()
        st=np.where(src,q[src].max(initial=0),q[~src].max(initial=0))
        result[left:right,7]=st;result[left:right,8]=q-st
    return result

def predict(d,p,model_path):
    c=context(d,p);model=lgb.Booster(model_file=str(model_path));result=np.empty(len(p),np.float32)
    for start in range(0,len(p),100000):
        end=min(start+100000,len(p));x=np.column_stack((np.asarray(d['X'][start:end]),c[start:end]))
        z=logits(p[start:end])+model.predict(x,raw_score=True,num_threads=2)
        result[start:end]=1/(1+np.exp(-np.clip(z,-40,40)))
    return result
