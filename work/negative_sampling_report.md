# Negative sampling report

All recovered positives are retained. Calibration scores every candidate.

| Policy | Rows | Positives | Negatives | S2 | S3 | India | US | S1 | SHA-256 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| hard | 113,411 | 15,072 | 98,339 | 59,751 | 53,660 | 44,911 | 68,500 | 4,997 | `b97d10a0d736f600e28c7e9b225b60ead712b36827a10f5deb8a7552137d07dd` |
| random | 113,411 | 15,072 | 98,339 | 56,702 | 56,709 | 44,911 | 68,500 | 4,997 | `8d3a65b55d366dd225039076e976cc58096ea1087483ac1c16b8f8d60629aab8` |
| hybrid_source_balanced | 113,367 | 15,072 | 98,295 | 56,456 | 56,911 | 44,894 | 68,473 | 4,997 | `c34c0f65ac652d51be4e940d4234f3c56058787d065f97091be2864e0fc7b3a8` |

Hard and random use 20 negatives/S1. The selected hybrid uses up to 10 hard-ranked negatives from each target source.
