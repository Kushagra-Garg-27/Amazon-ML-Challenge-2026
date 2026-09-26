# Baseline blocker: exact (country_norm, name_nosuffix), VAL split

- val S1=220531, targets S2=5034616 + S3=5285603 = 10320219
- total candidate pairs=8555794
- candidate size/S1: avg=38.80 median=3 p90=78 p95=167 p99=650 max=1424
- zero-candidate val S1=18791 (8.52%)
- brute-force space (val S1 x targets)=2275928216289
- reduction ratio=1 - 8555794/2275928216289 = 0.99999624

## Recall (GT pairs for val S1)

- total true val pairs=763919, covered=359166, true-pair recall=0.4702
- lost GT links=404753
- val S1 with >=1 true match=208254; with >=1 covered=172723 (S1-level recall=0.8294)

## Per-country true-pair recall (baseline key)
  - us: recall=0.4910 (225323/458902)
  - india: recall=0.4388 (133843/305017)

## Diagnostic — (country_norm, name_sorted) word-order-invariant key
- true-pair recall=0.5061 (vs 0.4702 baseline), total candidate pairs=9060998 (vs 8555794)
