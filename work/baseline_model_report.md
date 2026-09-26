# Pilot baseline model report

Calibration-pilot results only. Candidate oracle macro F0.5: **0.942885**.

| Model | Threshold | Macro F0.5 | Pair precision | Pair recall | Avg predicted | Singleton accuracy |
|---|---:|---:|---:|---:|---:|---:|
| Deterministic | 0.56 | 0.628979 | 0.928970 | 0.426153 | 1.612 | 0.877193 |
| Logistic | 0.93 | 0.781286 | 0.859481 | 0.702334 | 2.872 | 0.763158 |
| LightGBM smoke | 0.67 | 0.851852 | 0.936502 | 0.734491 | 2.756 | 0.912281 |

The fixed-seed NumPy linear logit was used because Windows application control blocked scikit-learn's compiled `_cd_fast` DLL. Training: 113,367 sampled rows in 1.15s. The LightGBM smoke used one fixed 40-tree, 15-leaf configuration; no search was run.
