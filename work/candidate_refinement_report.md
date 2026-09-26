# Candidate generation completion and freeze report

## 1. Frozen inputs and run state

The development split was preserved. It contains 220,531 validation S1 entities, 208,254 S1 entities with GT, and 763,919 GT links.

| Frozen item | SHA-256 |
|---|---|
| `work/split_s1.parquet` | `d01e4811836db39406a4dc33112a3a4a5105802a1fbbc21f9756d1d8bd3b0f2f` |
| `src/er/normalize.py` | `b3508b600e05e6008ceb7872d7b48e715df935ee7c4bb586d1cd7d5ec02ccedd` |
| `src/er/evaluate.py` | `e99d7f0ad83a582cba21f5d76d42b5f9ff5822740d5e962ec9759d071cc0441e` |
| `work/evidence_ranker_v1.json` | `a0fb606f9aed5bfef2f62184c1b5417d3936633c767326fe948907a193bf6e3c` |
| `work/final_candidate_policy.json` | `46fd324b3d4db7dbdf5fa4b8ce528f681335ce16f759fb93a39ba5b98a040fea` |
| production candidate package aggregate | `e3628daa37ed63f693dc88028152ef740900198c99d8e802b088a168674a7e09` |

All 128 evidence-rank shards are readable: 106,487,165 rows, 736,505,228 bytes, manifest checksum `c0740bb1f9c6928f9ab845941a12b539028aba9b6e70403a01d8e67b60525213`. Three incomplete source-75/75 parts without Parquet footers were quarantined and regenerated. At final inspection there were no Python or DuckDB jobs, DuckDB temp use was 0 bytes, free RAM was 3,663,347,712 bytes, and free disk was 231,761,620,992 bytes. This directory is not a Git worktree, so there is no repository diff; changed files are listed in section 13.

The canonical loss is kept in two exclusive artifacts: 43,959 key-ineligible links and 61,897 eligible-but-pruned/ranked links, totaling 105,856. Oracle counts never appear as materialized policy recall.

## 2. Exact evidence ranker

Target DF is the number of distinct S2/S3 target entities for `(country, token)`, separately for normalized name and address tokens; eligible DF is at most 2,000. For the active token pass:

- `score = sum(1 / target_df(token))` over distinct shared eligible tokens;
- shared token count, S1 coverage, target coverage, and token Jaccard are calculated from distinct tokens;
- exact name/address require equal non-empty frozen normalized values;
- postal evidence is equality of the first non-empty 5–6 digit address sequence;
- numeric evidence is equality of the first non-empty numeric address sequence;
- `both_pass` means inclusion in both name-token and address-token top-200-per-source shortlists.

The fixed order is `both_pass DESC, postal_shared DESC, numeric_shared DESC, exact_name DESC, exact_addr DESC, round(score,12) DESC, shared_token_count DESC, coverage_s1 DESC, coverage_target DESC, token_jaccard DESC, target_entity_id ASC`. Missing exact/numeric evidence is false. Zero-denominator coverage/Jaccard is null and sorts last. Source is used for quotas, not as identity evidence. No GT label, validation-fitted coefficient, test label, or external identity data enters ranking; all fields exist at inference.

## 3. Completed materialized policies

Resources are whole-process maxima. For policies whose generation and direct audit were separate, the table uses the larger RSS/temp and summed runtime. Canonical resources cover evaluation of the existing artifact.

| Policy | Candidates | Recovered GT | Recall | S1 cov. | Mean | P95 | P99 | Max | Gain | Loss | RSS MiB | Temp GiB | Size MiB | Runtime s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| canonical IDF-100 | 39,241,986 | 658,063 | .861430 | .975616 | 177.94 | 272 | 770 | 1,538 | 0 | 0 | 855.7 | 2.15 | 629.3 | 54.66 |
| evidence joint-75 | 31,603,760 | 667,957 | .874382 | .978704 | 143.31 | 247 | 733 | 1,512 | 21,214 | 11,320 | 864.2 | 1.80 | 191.6 | 103.09 |
| evidence joint-100 | 38,950,964 | 671,555 | .879092 | .979535 | 176.62 | 271 | 755 | 1,537 | 22,718 | 9,226 | 844.2 | 2.07 | 233.6 | 112.63 |
| source 37/38 | 31,587,753 | 668,132 | .874611 | .978713 | 143.23 | 247 | 733 | 1,512 | 21,334 | 11,265 | 908.5 | 1.79 | 191.7 | 225.95 |
| source 50/50 | 38,928,944 | 671,759 | .879359 | .979520 | 176.52 | 271 | 755 | 1,537 | 22,882 | 9,186 | 843.7 | 2.08 | 233.4 | 180.79 |
| source 75/75 | 53,035,472 | 677,352 | .886680 | .980740 | 240.49 | 321 | 801 | 1,587 | 25,868 | 6,579 | 883.7 | 2.47 | 314.3 | 80.83 |
| evidence dynamic-K | 41,816,190 | 673,355 | .881448 | .980461 | 189.62 | 313 | 788 | 1,587 | 23,763 | 8,471 | 913.4 | 2.16 | 251.0 | 104.85 |
| joint-75 + heavy100 | 27,219,525 | 666,968 | .873087 | .978377 | 123.43 | 173 | 181 | 245 | 21,214 | 12,309 | 861.6 | 1.55 | 165.2 | 42.80 |
| joint-100 + heavy100 | 34,588,087 | 670,583 | .877820 | .979222 | 156.84 | 201 | 212 | 293 | 22,718 | 10,198 | 844.6 | 1.91 | 207.2 | 49.98 |
| source 37/38 + heavy100 | 27,206,430 | 667,148 | .873323 | .978387 | 123.37 | 173 | 182 | 245 | 21,334 | 12,249 | 867.3 | 1.55 | 165.3 | 44.01 |
| **source 50/50 + heavy100** | **34,568,979** | **670,785** | **.878084** | **.979198** | **156.75** | **201** | **213** | **292** | **22,882** | **10,160** | **840.2** | **1.91** | **207.1** | **50.44** |
| joint-75 + heavy50 | 26,419,004 | 666,713 | .872754 | .978305 | 119.80 | 153 | 181 | 213 | 21,214 | 12,564 | 867.4 | 1.52 | 159.8 | 42.94 |
| joint-100 + heavy50 | 33,789,079 | 670,329 | .877487 | .979155 | 153.22 | 201 | 212 | 263 | 22,718 | 10,452 | 843.8 | 1.88 | 201.8 | 49.81 |
| sister-expanded canonical | 39,286,897 | 658,550 | .862068 | .975616 | 178.15 | 272 | 770 | 1,538 | 487 | 0 | 863.4 | 2.18 | 388.0 | 78.71 |

Median/p90 and zero-candidate S1 counts are respectively: canonical 188/206/230; joint-75 145/157/230; joint-100 186/203/230; 37/38 145/157/230; 50/50 185/203/230; 75/75 235/301/230; dynamic 195/298/230; joint-75 heavy100 145/155/230; joint-100 heavy100 182/200/230; 37/38 heavy100 145/155/230; 50/50 heavy100 181/200/230; joint-75 heavy50 138/150/230; joint-100 heavy50 164/200/230.

## 4. Direct canonical gain/loss

Every row uses candidate identity anti-joins and direct GT joins.

| Policy | Candidate intersection | Added | Removed | GT intersection | GT gained | GT lost | Net GT |
|---|---:|---:|---:|---:|---:|---:|---:|
| joint-75 | 20,202,370 | 11,401,390 | 19,039,616 | 646,743 | 21,214 | 11,320 | +9,894 |
| joint-100 | 22,996,156 | 15,954,808 | 16,245,830 | 648,837 | 22,718 | 9,226 | +13,492 |
| source 37/38 | 20,193,334 | 11,394,419 | 19,048,652 | 646,798 | 21,334 | 11,265 | +10,069 |
| source 50/50 | 22,982,885 | 15,946,059 | 16,259,101 | 648,877 | 22,882 | 9,186 | +13,696 |
| source 75/75 | 27,248,377 | 25,787,095 | 11,993,609 | 651,484 | 25,868 | 6,579 | +19,289 |
| dynamic | 23,691,269 | 18,124,921 | 15,550,717 | 649,592 | 23,763 | 8,471 | +15,292 |
| joint-75 heavy100 | 15,818,135 | 11,401,390 | 23,423,851 | 645,754 | 21,214 | 12,309 | +8,905 |
| joint-100 heavy100 | 18,633,279 | 15,954,808 | 20,608,707 | 647,865 | 22,718 | 10,198 | +12,520 |
| source 37/38 heavy100 | 15,812,011 | 11,394,419 | 23,429,975 | 645,814 | 21,334 | 12,249 | +9,085 |
| **source 50/50 heavy100** | **18,622,920** | **15,946,059** | **20,619,066** | **647,903** | **22,882** | **10,160** | **+12,722** |
| joint-75 heavy50 | 15,017,614 | 11,401,390 | 24,224,372 | 645,499 | 21,214 | 12,564 | +8,650 |
| joint-100 heavy50 | 17,834,271 | 15,954,808 | 21,407,715 | 647,611 | 22,718 | 10,452 | +12,266 |

## 5. Source, country, address missingness, and candidate-source results

Values are direct recall; `S2c/S3c` are candidate counts in millions.

| Policy | S2 | S3 | India | US | Both addr | Either missing | S2c | S3c |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| canonical | .866181 | .856994 | .854811 | .865830 | .866667 | .747078 | 19.282 | 19.960 |
| joint-75 | .881595 | .867646 | .872017 | .875954 | .880753 | .735270 | 19.809 | 11.794 |
| joint-100 | .886051 | .872593 | .875791 | .881286 | .885101 | .747885 | 23.700 | 15.251 |
| source 37/38 | .877564 | .871854 | .872165 | .876237 | .880971 | .735749 | 15.389 | 16.198 |
| source 50/50 | .882709 | .876231 | .875997 | .881593 | .885376 | .747975 | 19.204 | 19.725 |
| source 75/75 | .889773 | .883792 | .881577 | .890072 | .892272 | .764595 | 26.243 | 26.793 |
| dynamic | .885211 | .877934 | .878200 | .883607 | .887555 | .748094 | 20.645 | 21.172 |
| joint-75 heavy100 | .880483 | .866181 | .871830 | .873923 | .880263 | .716408 | 18.069 | 9.150 |
| joint-100 heavy100 | .884967 | .871145 | .875607 | .879290 | .884633 | .729053 | 21.964 | 12.624 |
| source 37/38 heavy100 | .876441 | .870411 | .871978 | .874217 | .880485 | .716946 | 13.622 | 13.585 |
| **source 50/50 heavy100** | **.881595** | **.874805** | **.875810** | **.879595** | **.884902** | **.729202** | **17.447** | **17.122** |
| joint-75 heavy50 | .880003 | .865983 | .871761 | .873413 | .880253 | .708995 | 17.475 | 8.944 |
| joint-100 heavy50 | .884487 | .870950 | .875538 | .878782 | .884624 | .721639 | 21.369 | 12.420 |

## 6. Heavy-block combinations

All five requested combinations were directly materialized. Relative to their uncapped base, heavy100 removes 4,384,235 candidates/989 GT for joint-75, 4,362,877/972 for joint-100, 4,359,965/974 for source 50/50, and 4,381,323/984 for source 37/38. Heavy50 removes 5,184,756/1,244 for joint-75 and 5,161,885/1,226 for joint-100. These are overlap-aware differences from the actual artifacts. Exact-address and multi-provenance candidates remain uncapped. The selected heavy100 policy reduces p99 from 755 to 213 and max from 1,537 to 292.

## 7. Evidence-aware dynamic K

The predeclared inference rule assigns slots per source and per token pass:

- K=25 if any target is retrieved by both exact sorted name and exact address;
- K=50 if there is any exact-address candidate or 1–5 exact sorted-name candidates;
- K=75 otherwise.

It is deterministic, bounded at 75 per source/pass, and uses no GT. Bands: K25 has 20,830 S1, 2,359,723 candidates, 80,524/87,338 GT (.921981); K50 has 121,236 S1, 19,480,629 candidates, 389,142/432,041 (.900706); K75 has 78,465 S1, 19,975,838 candidates, 203,689/244,540 (.832948). Overall: 41,816,190 candidates and 673,355 GT (.881448). Against joint-75 it gains 6,046 GT and loses 648 (net +5,398); against joint-100 it gains 3,291 and loses 1,491 (net +1,800). A max-75/source audit found 3,997 GT links clipped by the lower dynamic bands; all had required source ranks above the assigned band. Tail is p95 313, p99 788, max 1,587.

## 8. Sister-target expansion

The bounded label-free experiment used 39,866 deterministic seeds, target-frequency guards, and a cap of 20 added rows per S1. It added 44,911 candidates and 487 GT links (10.844 GT/1,000), with p95/p99/max added rows 16/20/20. S2 added 14,168 candidates/1 GT; S3 added 30,743/486. India added 26,799/397; US added 18,112/90. Overall tail remained p95 272, p99 770, max 1,538. The expansion is rejected because evidence policies recover far more GT at better or comparable total candidate cost; it is not part of the freeze.

## 9. Materialized Pareto frontier

Candidate-count/recall frontier rows are: joint-75 heavy50; source 37/38 heavy100; source 37/38; joint-100 heavy50; source 50/50 heavy100; source 50/50; dynamic-K; source 75/75. Joint-75 heavy100, joint-75, joint-100 heavy100, joint-100, canonical, and sister-expanded canonical are dominated by a completed row with no more candidates and at least as much recall. Heavy-tail behavior favors the heavy-cap policies strongly where totals are comparable.

Selected policies:

1. **Final:** source 50/50 + heavy100, 34.569M candidates, .878084 recall, p99 213, max 292.
2. **Compact fallback:** source 37/38 + heavy100, 27.206M, .873323, p99 182, max 245.
3. **Recall-leaning fallback:** source 75/75, 53.035M, .886680, p99 801, max 1,587.

## 10. Full-scale feasibility projection

Assumptions: validation candidate density transfers to the full 2,206,821 training S1 and 1,732,544 test S1; observed Parquet bytes/candidate transfer; a feature row is approximately 96 bytes before compression; a float32 score is 4 bytes; target distributions can shift, especially on test. Times below project only final policy materialization from prepared ranks using measured seconds/candidate.

| Policy/split | Projected candidates | Identity/prov | Features @96 B | Scores @4 B | Materialization |
|---|---:|---:|---:|---:|---:|
| final/train | 345.9M | 2.17 GB | 33.21 GB | 1.38 GB | 8.4 min |
| final/test | 271.6M | 1.71 GB | 26.07 GB | 1.09 GB | 6.6 min |
| compact/train | 272.3M | 1.73 GB | 26.14 GB | 1.09 GB | 7.3 min |
| compact/test | 213.7M | 1.36 GB | 20.52 GB | 0.85 GB | 5.8 min |
| recall/train | 530.7M | 3.30 GB | 50.95 GB | 2.12 GB | 13.5 min |
| recall/test | 416.7M | 2.59 GB | 40.00 GB | 1.67 GB | 10.6 min |

Proposed compact features are name/address exact flags, shared-token counts, IDF sums, both coverages and Jaccards, postal/numeric agreement, provenance, source, country agreement, string lengths, and missingness indicators. Use country plus stable S1 hash partitions: 128 for full training and 96 for test, one DuckDB thread and 800 MB memory per process, atomic `.partial` files, per-part checksums, and resume only after Parquet readability/count checks. Expected peak RSS is under 1.1 GiB based on the 840.2 MiB selected validation run and 648.5 MiB production reproduction. Reserve 80 GB temp for training and 64 GB for test, providing roughly 2× the linearized observed/ranking spill. Rank construction remains the longer stage; observed representative shards were 0.30–0.53 GiB RSS for evidence ranks, while earlier address-token ranks peaked near 0.97 GiB and 3.34–4.26 GB temp.

A test smoke materialization was skipped: no frozen normalized test key artifacts exist, so producing them would extend this pass into full test preprocessing. Feasibility is established from measured validation density, bytes/candidate, time/candidate, partition resource peaks, and the successful 16-part production reproduction. No test labels were read.

## 11. Restart reproducibility and canonical ties

Clean-process rerun of `rank_evidence_name_india_0` produced 383,523 rows, zero rank differences, and identical candidate identities/ranks. Original and rerun physical SHA-256 are both `32e0d6bc5989349057ccb312b401b0efaa910c1d34bb3b3bb0d17a4424e52dae`; RSS was 299.9 MiB and exit code 0. The selected production policy then reproduced 34,568,979 rows with 0 added, 0 removed, and 0 duplicate identities. Its 16-part manifest checksum is `221fef2400511bf14c5e2c167b44adea7cad7eac3df2b786eb0bc8ad9465b067`.

The old reconstructed IDF-100 set had 1,436 added and 1,437 missing candidates because dense ties lacked an explicit final order. That artifact remains only the canonical comparison. The new ranker always ends with ascending target identity.

## 12. Freeze artifacts and commands

- `work/final_candidate_policy.json`: machine-readable policy;
- `work/final_candidate_policy.md`: policy explanation;
- `work/final_candidate_policy_manifest.json`: frozen checksums and commands;
- `work/final_candidate_artifact_manifest.json`: 16 file hashes, rows, sizes, and identity proof;
- `work/final_candidate_policy_parts/part-*.parquet`: selected post-pruning candidate set;
- `work/evidence_rank_shard_manifest.json`: 128 rank-shard hashes;
- `work/final_candidate_policy_reproduction.log` and `work/final_candidate_policy_identity_check.log`: resource/run proof.

```powershell
$env:PYTHONPATH='code/business_entity_resolution/src'
.venv\Scripts\python.exe scripts/measure_peak.py --module er.candidates.materialize -- --config work/final_candidate_policy.json --rank-root work/freeze_gate --output-dir work/final_candidate_policy_parts --no-resume
.venv\Scripts\python.exe scripts/measure_peak.py --module er.candidates.audit -- --reference work/freeze_gate/evidence_source_50_50_heavy100.parquet --parts work/final_candidate_policy_parts --output work/final_candidate_artifact_manifest.json
```

Production reproduction: 151.64 s, 648.5 MiB RSS, 0 temp spill, exit 0. Identity verification: 915.1 MiB RSS, exit 0.

## 13. Production extraction and tests

Reusable code now lives in `src/er/candidates/{ranking,policies,materialize,audit}.py`; `scripts/freeze_gate.py` is a thin CLI, and historical stages moved to `src/er/candidates/experiments.py`. Added `tests/test_candidate_production.py`; updated `tests/test_freeze_gate.py`. The production module has direct tests for policy validation, evidence order, quotas/bands, provenance union, identity deduplication, one-to-many preservation, heavy-cap behavior, direct GT recall, manifest validation, and experimental/production artifact equivalence.

Test command:

```powershell
$env:PYTHONPATH='code/business_entity_resolution/src'
.venv\Scripts\python.exe -m unittest discover -s code/business_entity_resolution/tests -p 'test_*.py' -v
```

Raw summary: `Ran 86 tests in 24.249s` / `OK`. Passed 86, failed 0, exit code 0. Full output is in `work/final_candidate_test.log`.

## 14. Stop condition

The candidate freeze gate passes because the selected production policy reproduced an actual materialized candidate set with zero candidate-identity differences. No matcher was trained, no match threshold was tuned, no model feature vectors were generated, and no `matching_results.tsv` was generated.
