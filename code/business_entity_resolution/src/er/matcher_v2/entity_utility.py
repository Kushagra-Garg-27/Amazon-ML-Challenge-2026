"""Portable forests predicting per-entity utility of bounded threshold choices."""
import numpy as np
from .data import FEATURES

SUMMARY_NAMES=['top','second','third','gap','candidate_log_count','score_sum','high_count',
 's2_top','s3_top','s2_log_count','s3_log_count','missing_fraction','cross_max','cross_mean',
 'top_name','top_address','top_cross','top_missing','top_script','score_mean']

def summary_features(d,scores,size=100000):
    # No label or truth field is accessed here.
    entity=d['entity'];x=np.zeros((size,len(SUMMARY_NAMES)),np.float32)
    names={n:FEATURES.index(n) for n in ('cross_joint','name_ratio','address_ratio','script_conflict')}
    borders=np.r_[0,np.flatnonzero(entity[1:]!=entity[:-1])+1,len(entity)]
    for left,right in zip(borders[:-1],borders[1:]):
        if left==right:continue
        group=int(entity[left]);p=scores[left:right];order=np.argsort(-p,kind='stable');top=left+order[0]
        peaks=np.zeros(3);peaks[:min(3,len(p))]=p[order[:3]]
        src=d['source'][left:right];cross=np.asarray(d['X'][left:right,names['cross_joint']])
        x[group]=[*peaks,peaks[0]-peaks[1],np.log1p(len(p)),p.sum(),(p>=.8).sum(),
          p[src].max(initial=0),p[~src].max(initial=0),np.log1p(src.sum()),np.log1p((~src).sum()),
          d['address'][left:right].mean(),cross.max(initial=0),cross.mean(),
          d['X'][top,names['name_ratio']],d['X'][top,names['address_ratio']],d['X'][top,names['cross_joint']],
          d['address'][top],d['X'][top,names['script_conflict']],p.mean()]
    return x

def forest_artifact(model):
    trees=[]
    for estimator in model.estimators_:
        t=estimator.tree_
        trees.append({'left':t.children_left.tolist(),'right':t.children_right.tolist(),
          'feature':t.feature.tolist(),'threshold':t.threshold.tolist(),'value':t.value.reshape(t.node_count,-1).tolist()})
    return {'trees':trees,'input_features':SUMMARY_NAMES,'implementation':'sklearn RandomForestRegressor exported numerical trees'}

def predict_forest(artifact,x):
    x=np.asarray(x,dtype=np.float32);out=None
    for t in artifact['trees']:
        left=np.asarray(t['left']);right=np.asarray(t['right']);feature=np.asarray(t['feature'])
        threshold=np.asarray(t['threshold']);value=np.asarray(t['value']);nodes=np.zeros(len(x),np.int32)
        while True:
            rows=np.flatnonzero(left[nodes]>=0)
            if not len(rows):break
            node=nodes[rows];go_left=x[rows,feature[node]]<=threshold[node]
            nodes[rows]=np.where(go_left,left[node],right[node])
        if out is None:out=np.zeros((len(x),value.shape[1]))
        out+=value[nodes]
    return out/len(artifact['trees'])
