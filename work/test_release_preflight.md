# Test release preflight

PASS: frozen hashes, release decision, Python/LightGBM, feature order/threshold, resource guard, temp write, raw schemas, unique IDs and namespaces.

```json
{
  "timestamp_utc": "2026-09-26T19:09:09.564726+00:00",
  "checks": {
    "hashes": {
      "work/final_candidate_policy.json": {
        "expected": "46fd324b3d4db7dbdf5fa4b8ce528f681335ce16f759fb93a39ba5b98a040fea",
        "actual": "46fd324b3d4db7dbdf5fa4b8ce528f681335ce16f759fb93a39ba5b98a040fea"
      },
      "work/feature_spec_v1_1.json": {
        "expected": "37982fd6377aa2e36fe1c5dec0484a35c02ba4bcc4e3b67b3a337b33597f6e1f",
        "actual": "37982fd6377aa2e36fe1c5dec0484a35c02ba4bcc4e3b67b3a337b33597f6e1f"
      },
      "work/final_matcher_model.txt": {
        "expected": "76ff78a7cc97e40174b6d631a6eaa062c786f0480c6693fd6387e90be6761d21",
        "actual": "76ff78a7cc97e40174b6d631a6eaa062c786f0480c6693fd6387e90be6761d21"
      },
      "work/final_matcher_policy.json": {
        "expected": "ced4ee4133110f5f55c184c3d4020b5087ecac5398bdf9ebfd03f76b3662aba3",
        "actual": "ced4ee4133110f5f55c184c3d4020b5087ecac5398bdf9ebfd03f76b3662aba3"
      },
      "work/final_eval_release_gate_config.json": {
        "expected": "17165a4c083e73c747d5b57ca9e758959b0a8609d7306d738ad7ecb97326c6e9",
        "actual": "17165a4c083e73c747d5b57ca9e758959b0a8609d7306d738ad7ecb97326c6e9"
      },
      "code/business_entity_resolution/src/er/normalize.py": {
        "expected": "b3508b600e05e6008ceb7872d7b48e715df935ee7c4bb586d1cd7d5ec02ccedd",
        "actual": "b3508b600e05e6008ceb7872d7b48e715df935ee7c4bb586d1cd7d5ec02ccedd"
      }
    },
    "release_decision": "PASS",
    "python": "3.12.10",
    "lightgbm": "4.7.0",
    "feature_order_length": 61,
    "threshold": 0.61,
    "model_feature_order_matches": true,
    "candidate_policy": {
      "s2_quota": 50,
      "s3_quota": 50,
      "heavy_cap": 100,
      "df": {
        "name": 2000,
        "address": 2000
      },
      "hash_partitions": 16
    },
    "feature_spec_version": "feature_spec_v1.1",
    "free_disk_bytes": 211926466560,
    "free_ram_bytes": 3621949440,
    "temp_write_read_ok": true,
    "competing_python_duckdb_processes": [],
    "s2_s3_namespace_disjoint": true,
    "existing_test_suite": {
      "command": ".venv\\Scripts\\python.exe -m unittest discover -s code/business_entity_resolution/tests -v",
      "count": 120,
      "passed": true,
      "log": "work/test_release_preflight_suite.log"
    }
  },
  "raw": {
    "s1": {
      "path": "dataset\\test\\test_source1.tsv",
      "rows": 1732544,
      "distinct_ids": 1732544,
      "bad_prefix": 0,
      "name_missing": 0,
      "address_missing": 0,
      "countries": {
        "France": 259452,
        "India": 809986,
        "US": 663106
      }
    },
    "s2": {
      "path": "dataset\\test\\test_source2.tsv",
      "rows": 4887273,
      "distinct_ids": 4887273,
      "bad_prefix": 0,
      "name_missing": 0,
      "address_missing": 129408,
      "countries": {
        "France": 703378,
        "India": 2312565,
        "US": 1871330
      }
    },
    "s3": {
      "path": "dataset\\test\\test_source3.tsv",
      "rows": 5082316,
      "distinct_ids": 5082316,
      "bad_prefix": 0,
      "name_missing": 0,
      "address_missing": 136098,
      "countries": {
        "France": 731615,
        "India": 2405000,
        "US": 1945701
      }
    }
  },
  "status": "PASS"
}
```
