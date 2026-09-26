# Feature pilot report

Pilot: 7,000 S1s, 1,098,300 candidates, 65 features, 21,186 positives, 1,077,114 negatives (1.9290% positive).

Features occupy 36,956,489 bytes (33.65 bytes/candidate), below the prior 96-byte assumption. Identity audit: added 0, removed 0, duplicates 0. Null/NaN/inf counts are all zero.

Peak RSS: candidate generation 813.2 MiB; feature generation 530.7 MiB; audit 913.6 MiB. Feature wall time: 111.5s. Instrumented temp peak: 0 bytes.

Core feature time: exact 21.52s (19.60 µs/pair), token 7.05s (6.42 µs/pair), fuzzy 21.64s (19.70 µs/pair).

| Split | S1 | Candidates | Positives | Negatives |
|---|---:|---:|---:|---:|
| model_calibration | 2,000 | 315,559 | 6,114 | 309,445 |
| model_train | 5,000 | 782,741 | 15,072 | 767,669 |

Heavy-block rows: 109,629; S2/S3 rows: 555,286/543,014; address-missing rows: 20,420.
