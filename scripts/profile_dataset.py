#!/usr/bin/env python3
"""Stdlib-only streaming profiler for the Business Entity Resolution dataset.

Reads the large TSVs line-by-line (no pandas, low memory) and prints a concise
report: row counts, missing values, country distribution, name/address length &
token percentiles, sample rows, and ground-truth match-count structure.

Usage:
    python scripts/profile_dataset.py [--data-dir dataset]
"""
import argparse
import os
import sys
from collections import Counter

try:  # Windows consoles default to cp1252; the data is UTF-8 (transliterations, accents)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DELIM = "\t"


def summ(name, hist):
    """Percentile summary from a {value: count} histogram (bounded memory)."""
    if not hist:
        print(f"    {name}: (empty)")
        return
    total = sum(hist.values())
    keys = sorted(hist)
    mean = sum(k * c for k, c in hist.items()) / total

    def q(p):
        target = p / 100.0 * total
        cum = 0
        for k in keys:
            cum += hist[k]
            if cum >= target:
                return k
        return keys[-1]

    print(f"    {name}: min={keys[0]} p50={q(50)} p90={q(90)} "
          f"p95={q(95)} p99={q(99)} max={keys[-1]} mean={mean:.1f}")


def profile_source(path, label, n_samples=3):
    if not os.path.isfile(path):
        print(f"  [MISSING] {path}")
        return None
    rows = 0
    empt = Counter()
    countries = Counter()
    namelen = Counter()
    addrlen = Counter()
    ntok = Counter()
    samples = []
    with open(path, encoding="utf-8", errors="replace") as f:
        header = f.readline().rstrip("\n").split(DELIM)
        idx = {c.strip().lower(): i for i, c in enumerate(header)}
        i_nm = idx.get("business_name", 1)
        i_ad = idx.get("business_address", 2)
        i_ct = idx.get("country", 3)
        for line in f:
            parts = line.rstrip("\n").split(DELIM)
            if len(parts) < len(header):
                parts += [""] * (len(header) - len(parts))
            rows += 1
            nm = parts[i_nm].strip() if i_nm < len(parts) else ""
            ad = parts[i_ad].strip() if i_ad < len(parts) else ""
            ct = parts[i_ct].strip() if i_ct < len(parts) else ""
            if not nm:
                empt["business_name"] += 1
            if not ad:
                empt["business_address"] += 1
            if not ct:
                empt["country"] += 1
            countries[ct] += 1
            namelen[min(len(nm), 100000)] += 1
            addrlen[min(len(ad), 100000)] += 1
            ntok[min(len(nm.split()), 1000)] += 1
            if len(samples) < n_samples:
                samples.append(parts[:4])
    print(f"\n=== {label} :: {os.path.basename(path)} ===")
    print(f"  header: {header}")
    print(f"  rows: {rows:,}")
    print(f"  empty: name={empt['business_name']:,} "
          f"address={empt['business_address']:,} country={empt['country']:,}")
    print(f"  countries (top 10): {countries.most_common(10)}")
    print(f"  distinct country labels: {len(countries)}")
    summ("name_len", namelen)
    summ("addr_len", addrlen)
    summ("name_tokens", ntok)
    for s in samples:
        print(f"  sample: {s}")
    return {"rows": rows, "countries": countries}


def profile_ground_truth(path, label="TRAIN ground_truth"):
    if not os.path.isfile(path):
        print(f"  [MISSING] {path}")
        return
    rows = 0
    mc = Counter()
    total_matches = 0
    n_s2 = n_s3 = 0
    both_sources = 0
    multi_same_source = 0
    max_matches = 0
    samples = []
    with open(path, encoding="utf-8", errors="replace") as f:
        f.readline()  # header
        for line in f:
            s1, _, rest = line.rstrip("\n").partition(DELIM)
            rows += 1
            ids = [x for x in rest.split(",") if x.strip()] if rest.strip() else []
            k = len(ids)
            total_matches += k
            max_matches = max(max_matches, k)
            mc[k if k < 5 else "5+"] += 1
            s2 = sum(1 for x in ids if x.startswith("S2-"))
            s3 = sum(1 for x in ids if x.startswith("S3-"))
            n_s2 += s2
            n_s3 += s3
            if s2 and s3:
                both_sources += 1
            if s2 > 1 or s3 > 1:
                multi_same_source += 1
            if len(samples) < 5:
                samples.append((s1, ids))
    print(f"\n=== {label} :: {os.path.basename(path)} ===")
    print(f"  S1 rows: {rows:,}")
    print(f"  match-count distribution: "
          f"{dict(sorted(mc.items(), key=lambda x: str(x[0])))}")
    sing = mc.get(0, 0)
    print(f"  singletons (0 matches): {sing:,} ({100.0*sing/max(rows,1):.1f}%)")
    print(f"  total matches: {total_matches:,}  (S2={n_s2:,} S3={n_s3:,})")
    print(f"  max matches for one S1: {max_matches}")
    print(f"  S1 matching BOTH an S2 and an S3: {both_sources:,}")
    print(f"  S1 matching MULTIPLE within one source: {multi_same_source:,}")
    for s1, ids in samples:
        print(f"  sample: {s1} -> {ids}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="dataset")
    a = ap.parse_args()
    d = a.data_dir
    print("############ DATASET PROFILE ############")
    for split in ("train", "test"):
        for s in (1, 2, 3):
            profile_source(os.path.join(d, split, f"{split}_source{s}.tsv"),
                           f"{split.upper()} source{s}")
    profile_ground_truth(os.path.join(d, "train", "train_ground_truth.tsv"))


if __name__ == "__main__":
    main()
