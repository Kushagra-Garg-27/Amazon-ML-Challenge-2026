# S1 train/val split + leakage

- split_version=1 norm_version=1 seed=42 val_frac=0.1
- S1 total=2206821  train=1986290 (90.01%)  val=220531 (9.99%)
- val target-record leakage across split: 0 by construction (each S2/S3 matched to <=1 S1; see integrity_check.py)
- cross-split (country,name_nosuffix) collision keys: 55627
-   -> val entities sharing a name-key with a train entity: 102132 (46.312% of val)
- S1 rows with empty name_nosuffix key: 0
