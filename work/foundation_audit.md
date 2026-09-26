# Foundation audit — items 2 (leakage), 3 (lost-pair anatomy), 4 (baseline math)

Base: val GT pairs=763919, exact-key covered=359166 (recall=0.4702), lost=404753. [reproduces work/baseline_blocking.md]

## Item 4 — baseline math verified against a materialized distinct join

- exact candidate pairs via count-per-key (all val S1) = 8555794 [baseline_blocking.md reported 8,555,794]
- sample=4430 val S1 (deterministic hash%50=0): predicted-by-key=167861, materialized join rows=167861, distinct (s1,target)=167861
  → prediction EXACTLY matches the distinct join (targets are id-unique & S2/S3 disjoint ⇒ no double-count).

- exact⊆sorted check: candidates matching exact-key but NOT sorted-key = 0 (0 expected: name_sorted is sorted(set(tokens(name_nosuffix)))).
- sorted key STANDALONE: recall=0.5061 (386646/763919), candidate pairs=9060998 [baseline diag: 0.5061 / 9,060,998]
- UNION(exact,sorted): since exact⊆sorted, union == sorted. Incremental GT links recovered beyond exact = 27480; incremental candidate pairs = 505204.

## Item 3 — anatomy of the 404753 lost GT pairs (denominator shown per cut)

### by country (lost / country total pairs)
  - us: lost=233579/458902 (50.9% of that country's pairs)
  - india: lost=171174/305017 (56.1% of that country's pairs)

### name script per side (over lost)
  - S1: latin=404753 (100.0%)
  - target: latin=351818 (86.9%), deva=28779 (7.1%), other=21924 (5.4%), mixed=2232 (0.6%)
  - cross-script (one side deva-only, other latin-only) = 28779 (7.1% of lost) — DESCRIPTIVE fraction of lost pairs that are cross-script; NOT a formal transliteration ceiling (says nothing about what a transliteration blocker would actually retrieve)

### name_nosuffix token Jaccard (over lost)
  - Jaccard =0: 112058 (27.7%)
  - Jaccard (0,0.5): 155407 (38.4%)
  - Jaccard [0.5,1): 109808 (27.1%)
  - Jaccard =1: 27480 (6.8%)
  - sorted-name EQUAL (word-order-only difference) = 27480 (6.8%) — retrieved by the sorted-name blocker (an actual retrieval pass, measured below)

### address (addr_norm) over lost
  - both present: 389984 (96.4%)
  - only S1: 14769 (3.6%)
  - only target: 0 (0.0%)
  - neither: 0 (0.0%)
  address token Jaccard where both present (SIMILARITY OPPORTUNITY only — this is NOT retrieval; whether an address blocker actually returns these pairs is measured separately in the candidate-generation experiments):
    - Jaccard =0: 57 (0.0% of both-present)
    - Jaccard (0,0.5): 136846 (35.1% of both-present)
    - Jaccard [0.5,1): 190346 (48.8% of both-present)
    - Jaccard =1: 62735 (16.1% of both-present)

### exact-key target frequency for the lost pair's S1 (does S1 get candidates?)
  - key freq 0 (S1 has NO exact-key targets): 37952 (9.4%)
  - key freq 1-10: 239761 (59.2%)
  - key freq 11-100: 98214 (24.3%)
  - key freq >100: 28826 (7.1%)

## Item 2 — leakage assessment corrected + harder holdout materialized

- original split (PRESERVED at work/split_s1.parquet): train S1=1986290, val S1=220531
- WHY the old claim is overstated: 'each S2/S3 matches ≤1 S1' establishes only the observed LABELED-TARGET uniqueness property (no S2/S3 id is shared across S1 rows). It is NOT proof of no entity leakage: two DISTINCT S1 rows (one train, one val) can still be the same or a near-duplicate business, which target-uniqueness cannot exclude.

- cross-split (country,name_nosuffix) collision keys=55627; val S1 sharing a train name-key=102132 (46.312% of val) [manifest: 55,627 / 46.312%]
- collision TYPING on 42830 RARE keys (≤5 S1 each side; the other 12797 keys are generic/high-frequency names). Per key = MAX address token Jaccard over ALL cross-split S1 pairs (42830 keys have ≥1 both-address pair):
    - max addr Jac =0 (distinct businesses, same name): 24012 (56.1% of typed)
    - max addr Jac (0,0.5): 18810 (43.9% of typed)
    - max addr Jac [0.5,0.8): 8 (0.0% of typed)
    - max addr Jac [0.8,1) near-dup: 0 (0.0% of typed)
    - max addr Jac =1 identical addr: 0 (0.0% of typed)
  → 0 rare-name keys (0.0% of typed) have a cross-split S1 pair with address Jaccard≥0.8. SCOPE: this is evidence for the PROBED SUBSET only (rare keys, ≤5 S1/side, both addresses present). It does NOT generalize to the generic-name keys or to fuzzy/typo/translit name variants, and does NOT prove the split is leakage-free — it bounds address-corroborated near-duplication in this one measurable slice.

- HARDER HOLDOUT already materialized (FROZEN, not regenerated) → work/split_s1_grouped.parquet
  train S1=1982980 (89.86%), val S1=223841 (10.14%); cross-split name-key overlap = 0 (0 by construction)
  WHAT IT IS: a distribution-shift / stress-test diagnostic — S1 whose exact (country,name_nosuffix) key was never seen on the other side. It is NOT a guaranteed lower-bound score and NOT a leaderboard/competition estimate.
  LIMITS: (a) removes EXACT name-key overlap only, not fuzzy/near-dup overlap (typo/translit variants still bridge the split); (b) US/India only — France remains genuinely unseen; (c) grouping perturbs country mix slightly. Keep BOTH manifests: the random split for development/selection, this one as a distribution-shift diagnostic.

