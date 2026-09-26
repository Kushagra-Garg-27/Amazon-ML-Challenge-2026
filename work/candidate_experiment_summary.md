# Candidate experiment summary (DEVELOPMENT split)

val S1=220531; GT links=763919; S1 with GT=208254. Full evidence: work/candidate_experiments.md.

| pass | pair-recall | S1-coverage | distinct cands | cand/S1 mean | p99 | max |
|---|---|---|---|---|---|---|
| exact_name | 0.4702 | 0.8294 | 8555794 | 38.80 | 650.00 | 1424.00 |
| sorted_name | 0.5061 | 0.8562 | 9060998 | 41.09 | 670.00 | 1437.00 |
| exact_addr | 0.0832 | 0.2378 | 77772 | 0.35 | 3.00 | 20.00 |
| nametok | 0.5317 | 0.6004 | 11993715 | 54.39 | 100.00 | 100.00 |
| addrtok | 0.7965 | 0.8918 | 18920903 | 85.80 | 100.00 | 100.00 |

Selected union (sorted∪addr∪nametok∪addrtok) pair-recall=0.9425; final distinct candidates=39241986.
