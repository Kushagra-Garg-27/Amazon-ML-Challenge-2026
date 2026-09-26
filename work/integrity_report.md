# Dataset & GT Integrity Report (bounded-memory, DuckDB memory_limit=512MB)

## Per-source profile

| file | rows | distinct id | id unique? | empty name | empty addr | #countries |
|---|---|---|---|---|---|---|
| train_s1 | 2206821 | 2206821 | YES | 0 | 0 | 2 |
| train_s2 | 5034616 | 5034616 | YES | 0 | 168967 | 2 |
| train_s3 | 5285603 | 5285603 | YES | 0 | 175916 | 2 |
| test_s1 | 1732544 | 1732544 | YES | 0 | 0 | 3 |
| test_s2 | 4887273 | 4887273 | YES | 0 | 129408 | 3 |
| test_s3 | 5082316 | 5082316 | YES | 0 | 136098 | 3 |

## Country distribution (per file)

- **train_s1**: US=1323633, India=883188
- **train_s2**: US=3016817, India=2017799
- **train_s3**: US=3170056, India=2115547
- **test_s1**: India=809986, US=663106, France=259452
- **test_s2**: India=2312565, US=1871330, France=703378
- **test_s3**: India=2405000, US=1945701, France=731615

## Ground truth

- rows=2206821, distinct S1=2206821, duplicate-S1 rows=0, empty(singleton) rows=123247
- total matched pairs=7638365, avg matches/S1=3.461, max matches=11
- match-count dist: 0=123247 (5.6%), 1=119157 (5.4%), 2=375212 (17.0%), 3=530841 (24.1%), 4=484115 (21.9%), 5+=574249 (26.0%)
- matched-id prefix breakdown: S3-=3944746, S2-=3693619
- S1 rows containing a duplicate id within their own list: 0

## Referential integrity (GT ids exist in training sources?)

- GT S1 ids missing from train_source1: 0
- GT S2 matched-ids missing from train_source2: 0
- GT S3 matched-ids missing from train_source3: 0

## Cross-file uniqueness & match sharing

- train S2∩S3 entity_id value overlap: 0
- matched ids assigned to >1 distinct S1 (cross-S1 sharing / leakage risk): 0
