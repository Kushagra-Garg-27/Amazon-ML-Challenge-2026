# Test ingestion and normalization audit

PASS

```json
{
  "status": "PASS",
  "normalization_sha256": "b3508b600e05e6008ceb7872d7b48e715df935ee7c4bb586d1cd7d5ec02ccedd",
  "sources": {
    "s1": {
      "rows": 1732544,
      "unique_ids": 1732544,
      "missing_name": 0,
      "missing_address": 0,
      "countries": {
        "France": 259452,
        "India": 809986,
        "US": 663106
      },
      "missing_ids": 0,
      "extra_ids": 0,
      "partitioned_rows": 1732544,
      "partitions": 48,
      "replayed_sample": 100
    },
    "s2": {
      "rows": 4887273,
      "unique_ids": 4887273,
      "missing_name": 0,
      "missing_address": 129408,
      "countries": {
        "France": 703378,
        "India": 2312565,
        "US": 1871330
      },
      "missing_ids": 0,
      "extra_ids": 0,
      "partitioned_rows": 4887273,
      "partitions": 48,
      "replayed_sample": 100
    },
    "s3": {
      "rows": 5082316,
      "unique_ids": 5082316,
      "missing_name": 0,
      "missing_address": 136098,
      "countries": {
        "France": 731615,
        "India": 2405000,
        "US": 1945701
      },
      "missing_ids": 0,
      "extra_ids": 0,
      "partitioned_rows": 5082316,
      "partitions": 48,
      "replayed_sample": 100
    }
  }
}
```
