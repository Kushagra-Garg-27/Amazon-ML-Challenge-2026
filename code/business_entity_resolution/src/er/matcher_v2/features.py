"""Deterministic attribute-only features; no labels, fitted S1 maps or network."""
from functools import lru_cache
import re
import unicodedata
from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler,Levenshtein

@lru_cache(maxsize=30000)
def grams(s,n):
    return frozenset(s[i:i+n] for i in range(max(0,len(s)-n+1)))

def overlap(a,b):return len(a&b)/len(a|b) if a or b else 0.

def weighted_overlap(a,b,weights):
    a=set(a.split());b=set(b.split());union=a|b
    total=sum(weights.get(t,1.) for t in union)
    return sum(weights.get(t,1.) for t in a&b)/total if total else 0.

def name_features(a,b):
    valid=bool(a and b)
    aa=a.replace(' ','');bb=b.replace(' ','')
    initials=lambda s:''.join(t[0] for t in s.split() if t)
    return {'name_wratio':fuzz.WRatio(a,b)/100 if valid else 0.,
      'name_ng2':overlap(grams(a,2),grams(b,2)) if valid else 0.,
      'name_ng3':overlap(grams(a,3),grams(b,3)) if valid else 0.,
      'name_ng4':overlap(grams(a,4),grams(b,4)) if valid else 0.,
      'name_levenshtein':Levenshtein.normalized_similarity(a,b) if valid else 0.,
      'name_jaro':JaroWinkler.normalized_similarity(a,b) if valid else 0.,
      'name_compact_ratio':fuzz.ratio(aa,bb)/100 if valid else 0.,
      'initial_compat':float(valid and (initials(a)==initials(b) or aa==initials(b) or bb==initials(a))),
      'prefix_compat':float(valid and (aa.startswith(bb) or bb.startswith(aa))),
      'suffix_compat':float(valid and (aa.endswith(bb) or bb.endswith(aa)))}

_DEV_CONS=dict(zip('कखगघङचछजझञटठडढणतथदधनपफबभमयरलवशषसह',
 'k kh g gh ng ch chh j jh ny t th d dh n t th d dh n p ph b bh m y r l v sh sh s h'.split()))
_DEV_VOWELS=dict(zip('अआइईउऊऋएऐओऔ','a aa i ii u uu ri e ai o au'.split()))
_DEV_MARK=dict(zip('ािीुूृेैोौ','aa i ii u uu ri e ai o au'.split()))

@lru_cache(maxsize=30000)
def transliterate(s):
    result=[]
    for i,ch in enumerate(unicodedata.normalize('NFKD',s.lower())):
        if ch in _DEV_CONS:
            result.append(_DEV_CONS[ch])
            nxt=s[i+1] if i+1<len(s) else ''
            if nxt not in _DEV_MARK and nxt!='्':result.append('a')
        elif ch in _DEV_VOWELS:result.append(_DEV_VOWELS[ch])
        elif ch in _DEV_MARK:result.append(_DEV_MARK[ch])
        elif ch in 'ंँ':result.append('n')
        elif ch=='ः':result.append('h')
        elif ch=='्' or unicodedata.combining(ch):continue
        else:result.append(ch)
    value=''.join(result)
    # A deliberately limited local schwa heuristic, documented in the spec.
    return ' '.join(t[:-1] if len(t)>3 and t.endswith('a') and any('\u0900'<=c<='\u097f' for c in s) else t for t in value.split())

def script_features(a,b):
    x,y=transliterate(a),transliterate(b)
    return {'translit_ratio':fuzz.ratio(x,y)/100 if x and y else 0.,
      'translit_token_ratio':fuzz.token_sort_ratio(x,y)/100 if x and y else 0.,
      'translit_ng3':overlap(grams(x,3),grams(y,3)) if x and y else 0.}

def address_features(a,b):
    na=re.findall(r'\d+',a);nb=re.findall(r'\d+',b)
    pa=[x for x in na if len(x) in (5,6)];pb=[x for x in nb if len(x) in (5,6)]
    words=lambda s:frozenset(t for t in s.split() if not any(c.isdigit() for c in t))
    wa,wb=words(a),words(b)
    return {'addr_missing':float(not a or not b),
      'addr_weak':float(bool(a and b) and min(len(wa),len(wb))<2),
      'addr_ng3':overlap(grams(a,3),grams(b,3)) if a and b else 0.,
      'addr_levenshtein':Levenshtein.normalized_similarity(a,b) if a and b else 0.,
      'addr_locality_jaccard':overlap(wa,wb),
      'number_jaccard':overlap(set(na),set(nb)),
      'house_conflict_v2':float(bool(na and nb) and na[0]!=nb[0]),
      'postal_equal_v2':float(bool(set(pa)&set(pb))),
      'phone_equal_v2':float(bool({x for x in na if len(x)>=9}&{x for x in nb if len(x)>=9}))}

def cross_features(target,anchors):
    opposite=[a for a in anchors if a['mid'][:2]!=target['mid'][:2] and a['mid']!=target['mid'] and a['score']>=.61]
    values=[]
    for a in opposite[:2]:
        name=fuzz.ratio(target['name'],a['name'])/100 if target['name'] and a['name'] else 0.
        addr=fuzz.token_set_ratio(target['addr'],a['addr'])/100 if target['addr'] and a['addr'] else 0.
        trans=script_features(target['name'],a['name'])['translit_ratio']
        nums=overlap(set(re.findall(r'\d+',target['addr'])),set(re.findall(r'\d+',a['addr'])))
        values.append((name,addr,trans,nums,a['score']))
    return {'cross_name':max((v[0] for v in values),default=0.),
      'cross_address':max((v[1] for v in values),default=0.),
      'cross_translit':max((v[2] for v in values),default=0.),
      'cross_numeric':max((v[3] for v in values),default=0.),
      'cross_joint':max((min(v[0],v[1])*v[4] for v in values),default=0.),
      'cross_support_count':float(sum(v[0]>=.8 and (v[1]>=.6 or v[2]>=.9) for v in values)),
      'cross_seed_score':max((v[4] for v in values),default=0.)}

GROUPS={'name':list(name_features('a','b')),'script':list(script_features('a','b')),
        'address':list(address_features('a','b')),'cross':list(cross_features({'mid':'S2-a','name':'','addr':''},[])),
        'entity':['v1_score','candidate_count','source_candidate_count','top_score','second_score','score_gap','high_count','relative_to_top']}
