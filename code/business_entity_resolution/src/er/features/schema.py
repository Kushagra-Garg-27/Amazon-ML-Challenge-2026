"""Versioned numeric feature contract and persisted Arrow schema."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

FEATURE_SPEC_VERSION = "feature_spec_v1"

def _f(name, dtype, group, default, computation):
    return {"name":name,"type":dtype,"group":group,"default":default,"computation":computation}

FEATURES = [
 _f("provenance","uint8","provenance",0,"Frozen candidate provenance bitmask."),
 _f("retrieved_sorted_name","bool","provenance",False,"provenance & 1 != 0"),
 _f("retrieved_exact_address","bool","provenance",False,"provenance & 2 != 0"),
 _f("retrieved_name_token","bool","provenance",False,"provenance & 4 != 0"),
 _f("retrieved_address_token","bool","provenance",False,"provenance & 8 != 0"),
 _f("retrieval_pass_count","uint8","provenance",0,"Population count of bits 1,2,4,8."),
 _f("name_token_rank","uint16","provenance",0,"Frozen per-source name-token rank; zero when absent."),
 _f("address_token_rank","uint16","provenance",0,"Frozen per-source address-token rank; zero when absent."),
 _f("source_balanced_rank","uint16","provenance",0,"Minimum non-zero frozen token rank."),
 _f("heavy_sorted_block","bool","provenance",False,"Exact sorted-name target block has at least 120 rows."),
 _f("target_is_s2","bool","provenance",False,"Target namespace is S2."),
 _f("target_is_s3","bool","provenance",False,"Target namespace is S3."),
 _f("exact_name_norm","bool","exact",False,"Equal non-empty normalized name."),
 _f("exact_name_sorted","bool","exact",False,"Equal non-empty sorted-token name."),
 _f("exact_address_norm","bool","exact",False,"Equal non-empty normalized address."),
 _f("exact_name_nosuffix","bool","exact",False,"Equal non-empty suffix-stripped name."),
 _f("exact_numeric_set","bool","exact",False,"Equal non-empty address numeric-token sets."),
 _f("exact_postal_token","bool","exact",False,"Equal non-empty first 5-6 digit token."),
 _f("country_agreement","bool","exact",False,"Equal non-empty open-set normalized country."),
 _f("s1_name_missing","bool","missing_length",True,"S1 normalized name empty."),
 _f("target_name_missing","bool","missing_length",True,"Target normalized name empty."),
 _f("s1_address_missing","bool","missing_length",True,"S1 normalized address empty."),
 _f("target_address_missing","bool","missing_length",True,"Target normalized address empty."),
 _f("s1_name_chars","uint16","missing_length",0,"Normalized name character count."),
 _f("target_name_chars","uint16","missing_length",0,"Normalized name character count."),
 _f("s1_address_chars","uint16","missing_length",0,"Normalized address character count."),
 _f("target_address_chars","uint16","missing_length",0,"Normalized address character count."),
 _f("s1_name_tokens","uint16","missing_length",0,"Distinct suffix-stripped name tokens."),
 _f("target_name_tokens","uint16","missing_length",0,"Distinct suffix-stripped name tokens."),
 _f("s1_address_tokens","uint16","missing_length",0,"Distinct address tokens."),
 _f("target_address_tokens","uint16","missing_length",0,"Distinct address tokens."),
 _f("name_char_abs_diff","uint16","missing_length",0,"Absolute normalized-name length difference."),
 _f("address_char_abs_diff","uint16","missing_length",0,"Absolute normalized-address length difference."),
 _f("name_char_relative_diff","float32","missing_length",0.0,"Absolute difference divided by maximum length."),
 _f("address_char_relative_diff","float32","missing_length",0.0,"Absolute difference divided by maximum length."),
 _f("name_token_intersection","uint16","token",0,"Distinct name-token intersection size."),
 _f("name_token_union","uint16","token",0,"Distinct name-token union size."),
 _f("name_jaccard","float32","token",0.0,"Name intersection / union."),
 _f("name_containment_s1","float32","token",0.0,"Name intersection / S1 token count."),
 _f("name_containment_target","float32","token",0.0,"Name intersection / target token count."),
 _f("address_token_intersection","uint16","token",0,"Distinct address-token intersection size."),
 _f("address_token_union","uint16","token",0,"Distinct address-token union size."),
 _f("address_jaccard","float32","token",0.0,"Address intersection / union."),
 _f("address_containment_s1","float32","token",0.0,"Address intersection / S1 token count."),
 _f("address_containment_target","float32","token",0.0,"Address intersection / target token count."),
 _f("name_shared_idf","float32","token",0.0,"Sum 1/target-DF for shared eligible name tokens."),
 _f("address_shared_idf","float32","token",0.0,"Sum 1/target-DF for shared eligible address tokens."),
 _f("shared_address_numbers","uint16","numeric",0,"Count of shared distinct digit sequences."),
 _f("conflicting_address_numbers","bool","numeric",False,"Both have numbers and their sets are disjoint."),
 _f("shared_postal_tokens","uint8","numeric",0,"Equal non-empty first 5-6 digit token."),
 _f("conflicting_postal_tokens","bool","numeric",False,"Both have non-empty postal tokens that differ."),
 _f("digit_sequence_equal","bool","numeric",False,"Ordered digit-sequence lists are equal and non-empty."),
 _f("house_number_equal","bool","numeric",False,"First address number is equal and non-empty."),
 _f("name_ratio","float32","fuzzy",0.0,"RapidFuzz ratio / 100."),
 _f("name_partial_ratio","float32","fuzzy",0.0,"RapidFuzz partial_ratio / 100."),
 _f("name_token_sort_ratio","float32","fuzzy",0.0,"RapidFuzz token_sort_ratio / 100."),
 _f("name_token_set_ratio","float32","fuzzy",0.0,"RapidFuzz token_set_ratio / 100."),
 _f("address_ratio","float32","fuzzy",0.0,"RapidFuzz ratio / 100; zero if either address empty."),
 _f("address_partial_ratio","float32","fuzzy",0.0,"RapidFuzz partial_ratio / 100; zero if missing."),
 _f("address_token_sort_ratio","float32","fuzzy",0.0,"RapidFuzz token_sort_ratio / 100; zero if missing."),
 _f("address_token_set_ratio","float32","fuzzy",0.0,"RapidFuzz token_set_ratio / 100; zero if missing."),
 _f("s1_script_class","uint8","script",0,"0 empty, 1 Latin, 2 Devanagari, 3 mixed, 4 other."),
 _f("target_script_class","uint8","script",0,"0 empty, 1 Latin, 2 Devanagari, 3 mixed, 4 other."),
 _f("same_script_class","bool","script",False,"Equal non-zero script class."),
 _f("script_conflict","bool","script",False,"Different non-zero script classes."),
]

FEATURE_NAMES = tuple(x["name"] for x in FEATURES)
IDENTITY_COLUMNS = ("source1_entity_id","target_entity_id")

def validate_spec():
    if len(FEATURE_NAMES)!=len(set(FEATURE_NAMES)): raise ValueError("Duplicate feature names")
    if any(x["type"] not in {"bool","uint8","uint16","float32"} for x in FEATURES): raise ValueError("Unsupported type")

def write_spec(json_path: Path, md_path: Path):
    validate_spec(); payload={"schema_version":1,"feature_spec_version":FEATURE_SPEC_VERSION,"identity_columns":list(IDENTITY_COLUMNS),"label_column":None,"country_open_set":True,"features":FEATURES}
    json_path.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    lines=["# Feature specification v1","",f"Version: `{FEATURE_SPEC_VERSION}`. {len(FEATURES)} deterministic, label-free numeric features.","","Country is handled as open-set agreement only. Labels are stored separately. Candidate ranks are copied from frozen inference-time ranking artifacts and are never recomputed from labels.","","| Feature | Type | Group | Default | Computation |","|---|---|---|---:|---|"]
    for x in FEATURES: lines.append(f"| `{x['name']}` | {x['type']} | {x['group']} | `{x['default']}` | {x['computation']} |")
    md_path.write_text("\n".join(lines)+"\n",encoding="utf-8")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--json',type=Path,default=Path('work/feature_spec_v1.json')); ap.add_argument('--md',type=Path,default=Path('work/feature_spec_v1.md')); a=ap.parse_args(); write_spec(a.json,a.md); print(f"features={len(FEATURES)}")

if __name__=='__main__': main()
