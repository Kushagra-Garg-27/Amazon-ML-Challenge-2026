# Pilot baseline error analysis

At logistic threshold 0.93:

- false positive pairs: 807
- recovered positive below threshold: 1,178
- recovered candidate positives: 6,114
- false positive script conflict: 244
- false negative script conflict: 46
- strong name weak address fp: 97
- weak name strong address fn: 36
- candidate missed gt: 914
- end to end missed gt: 2,092
- singleton false positive s1: 27
- multi match underprediction s1: 1,122
- multi match overprediction s1: 297
- address missing recovered positive below threshold: 101
- s2 recovered positive below threshold: 493
- s3 recovered positive below threshold: 685

Candidate-generation misses and recovered candidates rejected by the matcher are disjoint populations. No raw business strings are included.
