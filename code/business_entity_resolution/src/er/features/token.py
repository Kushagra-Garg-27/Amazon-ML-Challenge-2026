"""Pure token overlap features."""
from __future__ import annotations

def _one(a: str,b: str,prefix: str) -> dict:
    x=set((a or '').split()); y=set((b or '').split()); inter=len(x&y); union=len(x|y)
    return {f'{prefix}_token_intersection':inter,f'{prefix}_token_union':union,
      f'{prefix}_jaccard':inter/union if union else 0.0,
      f'{prefix}_containment_s1':inter/len(x) if x else 0.0,
      f'{prefix}_containment_target':inter/len(y) if y else 0.0}

def token_features(s: dict,t: dict) -> dict:
    return {**_one(s.get('name_nosuffix',''),t.get('name_nosuffix',''),'name'),
            **_one(s.get('addr_norm',''),t.get('addr_norm',''),'address')}
