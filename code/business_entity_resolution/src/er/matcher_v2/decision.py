"""Per-S1 metric, deterministic expected-set policy and target conflict rules."""
import numpy as np

def entity_f05(truth,pred,tp):
    truth=np.asarray(truth);pred=np.asarray(pred);tp=np.asarray(tp)
    return np.divide(1.25*tp,.25*truth+pred,out=np.zeros_like(tp,dtype=float),where=(.25*truth+pred)>0)+( (truth==0)&(pred==0))

def choose_expected_set(probabilities):
    p=np.clip(np.asarray(probabilities),0,1)
    if not len(p):return np.zeros(0,bool)
    order=np.argsort(-p,kind='stable');q=p[order]
    expected=1.25*np.cumsum(q)/(.25*p.sum()+np.arange(1,len(p)+1))
    empty=np.prod(1-p)
    k=int(np.argmax(np.r_[empty,expected]))
    accepted=np.zeros(len(p),bool);accepted[order[:k]]=True
    return accepted

def resolve_conflicts(s1,target,score,accepted):
    result=np.zeros(len(score),bool);keep=np.flatnonzero(accepted)
    order=sorted(keep,key=lambda i:(str(target[i]),-float(score[i]),int(s1[i])))
    previous=None
    for i in order:
        if target[i]!=previous:result[i]=True;previous=target[i]
    return result
