# Model-development split firewall

Seed: `model_development_v1_20260926`.

The frozen top-level split is unchanged. Every previously used model-train pilot S1 is forced into `model_fit`. The remaining rows use the first byte of `md5(entity_id || seed)`: `00`-`07` for `model_tune`, `08`-`0f` for `model_threshold`, and all other bytes for `model_fit`.

The approximately 94/3/3 allocation is a resource-safe adjustment from the suggested 90/5/5 split. Each protected subset still contains more than 50,000 S1s.

| Split | S1 | ID checksum |
|---|---:|---|
| model_fit | 1,655,792 | `0677a4f86631f315a9a9e44d6d94e7e4d24f382f9f522d4db283b6b4fca9c659` |
| model_tune | 54,725 | `cda2f771e68a4647d37caa4bee6f20340bd1e35af5953fc0b0b296fa98654fae` |
| model_threshold | 55,091 | `318b2c77c328e11dd69f4f8b87210e434f1fd6aaded5c6354a845845c4c35a2e` |

S1 overlap: **0**. Target-ID overlap: **0**. Prior pilot rows outside model_fit: **0**.

`model_calibration` is reclassified as `baseline_dev`. It is absent from this file because the frozen top-level manifest remains authoritative.

At split creation, `model_threshold` has membership, country, count, and an ID checksum only. No threshold labels, candidates, features, predictions, or metrics were read or materialized.
