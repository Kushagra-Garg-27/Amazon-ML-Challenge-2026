# Development sample tiers

Nested deterministic model-fit tiers. Time values are density-based estimates from the directly measured threshold materialization; RSS and temp are measured shared-process peaks.

| Tier | S1 | Candidates | Positives | Oracle F0.5 | Pair recall | Mean | P95 | P99 | Max | Feature MB | Candidate s est. | Feature s est. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 5,000 | 782,741 | 15,072 | 0.944824 | 0.873587 | 156.55 | 201 | 212 | 288 | 26.3 | 38.3 | 59.6 |
| B | 20,000 | 3,127,724 | 60,539 | 0.948018 | 0.876221 | 156.39 | 201 | 212 | 289 | 105.2 | 152.9 | 238.0 |
| C | 50,000 | 7,833,281 | 151,102 | 0.947728 | 0.876731 | 156.67 | 201 | 213 | 290 | 263.6 | 382.8 | 596.0 |

All tiers contain India/US, S2/S3, missing-address, heavy-block, and all observed provenance-bit categories. Tier D was skipped because Tier C improved baseline-dev macro F0.5 by only 0.000879 over Tier B while more than doubling sampled rows.
