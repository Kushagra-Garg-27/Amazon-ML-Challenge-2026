"""Bounded RapidFuzz similarities."""
from rapidfuzz import fuzz

def fuzzy_features(s: dict,t: dict) -> dict:
    sn=s.get('name_norm') or ''; tn=t.get('name_norm') or ''; sa=s.get('addr_norm') or ''; ta=t.get('addr_norm') or ''
    def score(fn,a,b): return float(fn(a,b)/100.0) if a and b else 0.0
    return {'name_ratio':score(fuzz.ratio,sn,tn),'name_partial_ratio':score(fuzz.partial_ratio,sn,tn),
      'name_token_sort_ratio':score(fuzz.token_sort_ratio,sn,tn),'name_token_set_ratio':score(fuzz.token_set_ratio,sn,tn),
      'address_ratio':score(fuzz.ratio,sa,ta),'address_partial_ratio':score(fuzz.partial_ratio,sa,ta),
      'address_token_sort_ratio':score(fuzz.token_sort_ratio,sa,ta),'address_token_set_ratio':score(fuzz.token_set_ratio,sa,ta)}
