"""Pure exact, missingness, numeric and script pair features."""
from __future__ import annotations

import re
import unicodedata

POSTAL=re.compile(r"\d{5,6}")
DIGITS=re.compile(r"\d+")

def tokens(value: str) -> set[str]: return set((value or "").split())
def digit_sequence(value: str) -> tuple[str,...]: return tuple(DIGITS.findall(value or ""))
def postal(value: str) -> str:
    m=POSTAL.search(value or ""); return m.group(0) if m else ""

def script_class(value: str) -> int:
    if not value: return 0
    latin=dev=other=False
    for ch in value:
        if not ch.isalpha(): continue
        name=unicodedata.name(ch,"")
        if "LATIN" in name: latin=True
        elif "DEVANAGARI" in name: dev=True
        else: other=True
    kinds=sum((latin,dev,other))
    if kinds>1: return 3
    if latin: return 1
    if dev: return 2
    return 4 if other else 0

def exact_features(s: dict,t: dict) -> dict:
    sn=s.get('name_norm') or ''; tn=t.get('name_norm') or ''
    ss=s.get('name_sorted') or ''; ts=t.get('name_sorted') or ''
    sx=s.get('name_nosuffix') or ''; tx=t.get('name_nosuffix') or ''
    sa=s.get('addr_norm') or ''; ta=t.get('addr_norm') or ''
    sc=s.get('country_norm') or ''; tc=t.get('country_norm') or ''
    seqs=digit_sequence(sa); seqt=digit_sequence(ta); ns=set(seqs); nt=set(seqt); ps=postal(sa); pt=postal(ta)
    snc,tnc,sac,tac=len(sn),len(tn),len(sa),len(ta)
    sscript,tscript=script_class(sn),script_class(tn)
    return {
      'exact_name_norm':bool(sn and sn==tn),'exact_name_sorted':bool(ss and ss==ts),
      'exact_address_norm':bool(sa and sa==ta),'exact_name_nosuffix':bool(sx and sx==tx),
      'exact_numeric_set':bool(ns and ns==nt),'exact_postal_token':bool(ps and ps==pt),
      'country_agreement':bool(sc and sc==tc),'s1_name_missing':not bool(sn),
      'target_name_missing':not bool(tn),'s1_address_missing':not bool(sa),
      'target_address_missing':not bool(ta),'s1_name_chars':snc,'target_name_chars':tnc,
      's1_address_chars':sac,'target_address_chars':tac,
      's1_name_tokens':len(tokens(sx)),'target_name_tokens':len(tokens(tx)),
      's1_address_tokens':len(tokens(sa)),'target_address_tokens':len(tokens(ta)),
      'name_char_abs_diff':abs(snc-tnc),'address_char_abs_diff':abs(sac-tac),
      'name_char_relative_diff':abs(snc-tnc)/max(snc,tnc,1),
      'address_char_relative_diff':abs(sac-tac)/max(sac,tac,1),
      'shared_address_numbers':len(ns&nt),
      'conflicting_address_numbers':bool(ns and nt and ns!=nt),
      'shared_postal_tokens':int(bool(ps and ps==pt)),
      'conflicting_postal_tokens':bool(ps and pt and ps!=pt),
      'digit_sequence_equal':bool(seqs and seqs==seqt),'house_number_equal':bool(seqs and seqt and seqs[0]==seqt[0]),
      's1_script_class':sscript,'target_script_class':tscript,
      'same_script_class':bool(sscript and sscript==tscript),
      'script_conflict':bool(sscript and tscript and sscript!=tscript),
    }
