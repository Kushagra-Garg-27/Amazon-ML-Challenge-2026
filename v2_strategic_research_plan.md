# V2.1 / V3 STRATEGIC RESEARCH PLAN
## Amazon ML Challenge 2026 — Path Toward 0.99+ Macro F₀.₅

**Classification**: RESEARCH PLAN — NO IMPLEMENTATION  
**Date**: 2026-09-27  
**V1 public score**: 0.876359 (context only)  
**V1 internal final eval**: 0.896256  
**Visible leaderboard top**: 0.991811 (context only; NOT an optimization target)

**Phase 2 context amendment (2026-09-27):** The pre-experiment version of this plan is preserved at `work/v2_phase2_r1/strategic_plan_context_original.md` (SHA-256 `FED8357C75E277A508FBFC57E1D460E0D07421E4355C553B53B20CB513F6CBAE`). Historical measurements below remain as originally reported; proposed targets and projections are hypotheses. The Phase 2 measured addendum at the end supersedes prior experiment priorities and gates.

---

## A. EXECUTIVE CONCLUSION

Reaching 0.99+ macro F₀.₅ is **theoretically possible but unproven**. The gap is real and large.
Every layer of the system — retrieval, matching, and decision — must improve simultaneously.
No single fix is sufficient.

The evidence shows:

| Layer | V1 measured | Required for ~0.99 | Gap |
|:---|---:|---:|---:|
| Candidate pair recall | 87.90% | ~97–98% | ~10 pp |
| Candidate oracle F₀.₅ | 0.9496 | ~0.995 | ~0.045 |
| Matcher pair precision | 97.08% | ~99.0% | ~2 pp |
| Matcher pair recall (of recovered) | 91.4%¹ | ~97% | ~5.6 pp |
| Singleton accuracy | 89.39% | ~97% | ~7.6 pp |
| Multi-match underprediction S1 | 44,738 | <5,000 | ~90% reduction |

¹ Recovered-truth macro F₀.₅ is 0.9448; recovered pair recall = 306,775/335,657 = 91.4%.

**The dominant bottleneck is retrieval.** The candidate oracle ceiling (0.9496) means that
even a perfect matcher on the current candidate set cannot exceed ~0.95. Before any matcher
improvement matters, candidate recall must be pushed substantially higher.

**Historical next-experiment proposal, now measured in Phase 2**: Collective target-to-target expansion using inference-safe
high-confidence seeds, with controlled error propagation. This is the only known method
that addresses both the 19,897 "no V1 key" misses AND the 22,059 rank/cap losses simultaneously.

---

## B. CURRENT 0.99 ERROR BUDGET

Working backwards from a hypothetical 0.99 macro F₀.₅ target, using the measured V1 final-eval
population (110,341 S1; 381,856 truth pairs).

### B.1 Decomposition of V1 losses

| Error source | V1 count | V1 rate | Required for ~0.99 |
|:---|---:|---:|---:|
| **Candidate generation misses** | 46,199 pairs | 12.10% of truth | ≤ ~2–3% |
| **Matcher false negatives** (among recovered) | 28,882 pairs | 8.60% of recovered | ≤ ~3% |
| **Matcher false positives** | 9,212 pairs | 2.92% of predicted | ≤ ~1% |
| **Singleton FP S1** (no true match, predicted some) | 672 S1 | 10.61% of singletons | ≤ ~3% |
| **Multi-match underprediction S1** | 44,738 S1 | 43.0% of multi-match S1 | ≤ ~10% |
| **Multi-match overprediction S1** | 4,337 S1 | 4.2% of multi-match S1 | ≤ ~1.5% |

### B.2 Layer-by-layer loss allocation for 0.99

A 0.99 macro F₀.₅ requires controlling errors at every layer. Using measured data:

```
Target: 0.99 macro F₀.₅

Layer 1 — Retrieval:
  Current pair recall:          87.90%
  Current oracle F₀.₅:         0.9496
  Planning oracle heuristic:    ~0.993  (assumed ~0.7% matcher loss; not a theorem)
  → Requires pair recall:       ≥ ~97%

Layer 2 — Matcher discrimination:
  Current recovered-truth F₀.₅: 0.9448
  Required recovered-truth F₀.₅: ≥ 0.996
  → Pair precision:             ≥ 99.0%
  → Pair recall (of recovered): ≥ 96%

Layer 3 — Decision policy:
  Singleton accuracy:           89.39% → ≥ 97%
  Multi-match underprediction:  43.0%  → ≤ 10%
  Multi-match overprediction:    4.2%  → ≤ 1.5%
```

> [!IMPORTANT]
> An oracle near 0.993 was a planning heuristic under an assumed matcher loss budget.
> The achievable end-to-end score must be measured; this heuristic is not a proof of feasibility or impossibility.

### B.3 Country breakdown

| Country | V1 F₀.₅ | V1 Oracle | Required improvement |
|:---|---:|---:|---:|
| India | 0.877 | 0.945 | +0.113 |
| US | 0.909 | 0.953 | +0.081 |
| France | Unknown | Unknown | **Unmeasurable** |

India has a larger gap, driven by lower pair precision (0.958 vs 0.979 for US) and
lower singleton accuracy (0.867 vs 0.912). This suggests India-specific feature weakness
or higher name/address noise.

### B.4 Address-missingness impact

| Slice | Pair precision | Pair recall | Impact |
|:---|---:|---:|:---|
| Both addresses present | 0.975 | 0.828 | ~95% of pairs; strong signal |
| Either address missing | 0.766 | 0.270 | ~5% of pairs; **catastrophic recall** |

The "either address missing" slice has 16,954 truth pairs but only 4,573 TPs (recall 27%).
This accounts for 7,762 matcher FN — **27% of all matcher FN from 5% of truth**.

### B.5 Script conflict impact

| Slice | Pair precision | Pair recall | Impact |
|:---|---:|---:|:---|
| Same/empty script | 0.974 | 0.826 | Baseline behavior |
| Script conflict (Devanagari↔Latin) | 0.909 | 0.516 | **Severe recall collapse** |

Script-conflict truth: 27,818 pairs. Only 14,363 TP → 48.4% recall loss vs baseline.
Cross-script is a disproportionate error source.

### B.6 Candidate rank distribution

| Rank band | Recovered truth | Recall of recovered | Fraction of FN |
|:---|---:|---:|---:|
| Rank 0 (best candidate) | 32,358 | 81.2% | 21.1% of matcher FN |
| Rank 1–10 | 291,053 | 93.9% | 61.7% |
| Rank 11–25 | 7,045 | 58.6% | 10.1% |
| Rank 26–50 | 5,201 | 60.3% | 7.1% |

The bulk of true matches (87%) sit in rank 0–10 and recover well (93.9%). But the
"tail" candidates at rank 11+ have severely degraded recall (~59%), contributing ~17% of FN.

---

## C. RETRIEVAL CEILING ANALYSIS

### C.1 Current state

| Metric | V1 final eval | V2 research (100k S1) |
|:---|---:|---:|
| Candidate pairs | 17,295,784 | 15,649,461 |
| Pair recall | 87.90% | 87.84% |
| Oracle F₀.₅ | 0.9496 | 0.9487 |
| Missed GT links | 46,199 | 41,956 |

### C.2 Miss decomposition (measured on research)

| Category | Count | % of missed | Key characteristic |
|:---|---:|---:|:---|
| No eligible V1 retrieval key | 19,897 | 47.4% | Fundamentally unretrievable by V1 |
| Lost after eligibility (rank/cap) | 22,059 | 52.6% | Had a valid key; killed by shortlist |
| **Total** | **41,956** | **100%** | |

### C.3 Analysis by retrieval family

#### A. V1 retrieval misses (19,897 — no eligible key)

These 19,897 links had no matching token/name/address key with DF ≤ 2,000 in V1.

| Signal family | Coverage of the 19,897 | Inference-safe? |
|:---|---:|:---|
| Name 4-gram overlap | 32,236/41,956 total | Yes |
| Name token overlap | 25,012/41,956 total | Yes |
| Address 4-gram overlap | 37,744/41,956 total | Yes |
| Acronym equal | 4,515/41,956 total | Yes |
| True sister retrieved | 37,866/41,956 total | Sister-bridge only |
| Sister closer than S1 | 10,977/41,956 total | Sister-bridge only |
| Cross-source sister | 32,072/41,956 total | Sister-bridge only |
| Devanagari↔Latin | 4,854/41,956 total | Requires transliteration |

> [!NOTE]
> The signals above overlap heavily and are non-exclusive.
> The sister signal covers 90.3% of all misses — this is the richest evidence family.

#### B. Rank/cap losses (22,059)

These had an eligible V1 key but were eliminated by the shortlist cap (100 for heavy sorted name)
or per-source quotas (50/50). Research shows:

| Pass | Eligible V1 misses | New GT recovered | GT lost to quota |
|:---|---:|---:|---:|
| name_char4 | 6,693 | 2,215 | 4,478 |
| address_char4 | 3,132 | 2,406 | 726 |
| sister_expansion | 3,492 | 1,713 | 1,779 |
| acronym | 1,998 | 1,906 | 92 |
| postal_like_numeric | 382 | 339 | 43 |

Even the Phase-1 new passes still lose links to their own quotas (e.g., name_char4 loses
4,478 of 6,693 eligible V1 misses). This suggests **dynamic K / adaptive shortlists** could
recover substantial additional GT without adding new blocking keys.

#### C–L. Individual retrieval family analysis

| Family | Demonstrated links | Candidate cost | Precision risk | Feasibility |
|:---|---:|:---|:---|:---|
| **Collective sister expansion** | 37,866 with retrieved sister | Unknown (unbounded risk) | HIGH if uncontrolled | Phase 1 priority |
| **Dynamic K / adaptive cap** | 22,059 eligible | +0 new keys, +caps | LOW (same features) | Phase 2 priority |
| **Name 4-grams** | 2,215 incremental in Phase-1 | +1.7M pairs (11%) | Moderate | Already tested |
| **Address 4-grams** | 2,406 incremental | +0.7M pairs (4%) | Moderate | Already tested |
| **Acronym** | 1,906 incremental | +0.3M pairs (2%) | Low | Already tested |
| **Postal/numeric** | 339 incremental | +0.2M pairs (1%) | Low | Already tested |
| **Transliteration** | 4,854 upper bound | Unknown | HIGH | Deferred hypothesis |
| **Character n-grams (2,3)** | Profiled, not materialized | Very high | HIGH | Not recommended |

### C.4 Best materialized research point

```
v1_plus_all:  18,895,613 candidates (+3,246,152 over V1)
              90.081% pair recall  (+2.24 pp over V1 87.844%)
              0.958075 oracle F₀.₅ (+0.0094 over V1 0.948700)
              7,722 new GT links recovered (310,916 total recovered of 345,150)
              188.96 mean candidates/S1 (P90=259, P95=271, P99=291, max=380)
              Fully resolved S1: 71,543 (+4,769 over V1); Zero-recall S1: 1,610 (-351 over V1)
```

#### Materialized candidate research Pareto frontier:

| Policy | Candidates | Mean/S1 | P95 | P99 | Max | Pair recall | Oracle macro F₀.₅ | GT added | GT lost | GT/1000 added | Frontier |
|:---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---|
| **v1_baseline** | 15,649,461 | 156.49 | 201 | 212 | 292 | 0.878441 | 0.948700 | 0 | 0 | n/a | **YES** |
| name_char4_standalone | 2,561,979 | 25.62 | 50 | 50 | 50 | 0.260733 | 0.357476 | 2,215 | 215,417 | 1.303 | YES |
| acronym_standalone | 354,486 | 3.54 | 20 | 20 | 20 | 0.125957 | 0.226014 | 1,906 | 261,626 | 6.961 | YES |
| postal_like_numeric_standalone | 245,914 | 2.46 | 20 | 20 | 20 | 0.098279 | 0.166446 | 339 | 269,612 | 8.236 | YES |
| address_char4_standalone | 1,010,960 | 10.11 | 30 | 30 | 30 | 0.252116 | 0.330739 | 2,406 | 218,582 | 3.559 | YES |
| sister_expansion_standalone (1 seed) | 541,480 | 5.41 | 10 | 10 | 10 | 0.144612 | 0.284159 | 1,713 | 254,994 | 6.009 | YES |
| multiseed_sister_standalone (top 3) | 971,676 | 9.72 | 15 | 15 | 15 | 0.134846 | 0.255940 | 2,812 | 259,464 | 4.969 | NO |
| v1_plus_name_char4 | 17,349,353 | 173.49 | 246 | 251 | 333 | 0.884859 | 0.951333 | 2,215 | 0 | 1.303 | NO |
| **v1_plus_acronym** | 15,923,288 | 159.23 | 214 | 219 | 292 | 0.883963 | 0.951490 | 1,906 | 0 | 6.961 | **YES** |
| **v1_plus_postal_like_numeric** | 15,690,622 | 156.91 | 203 | 214 | 301 | 0.879423 | 0.949078 | 339 | 0 | 8.236 | **YES** |
| **v1_plus_address_char4** | 16,325,483 | 163.25 | 224 | 230 | 319 | 0.885412 | 0.951734 | 2,406 | 0 | 3.559 | **YES** |
| v1_plus_sister_expansion (1 seed) | 15,934,512 | 159.35 | 209 | 214 | 298 | 0.883404 | 0.950432 | 1,713 | 0 | 6.009 | YES |
| **v1_plus_multiseed_sister** (top 3) | 16,215,413 | 162.15 | 214 | 218 | 302 | 0.886588 | 0.951430 | 2,812 | 0 | 4.969 | **YES** |
| **v1_plus_all** | 18,895,613 | 188.96 | 271 | 291 | 380 | 0.900814 | 0.958075 | 7,722 | 0 | 2.379 | **YES** |

While breaking the 90% recall barrier is a key empirical milestone (+2.24 pp recall gain),
it remains well below the ~97% target needed for ~0.99. The remaining gap of ~7.0 pp
represents roughly 24,000+ unrecovered ground-truth links.

### C.5 Theoretical retrieval ceiling

| Scenario | Estimated pair recall | Oracle F₀.₅ | Empirical status |
|:---|---:|---:|:---|
| V1 baseline | 87.84% | 0.9487 | **MEASURED FACT** |
| V1 + multi-seed sister (top 3) | 88.66% | 0.9514 | **MEASURED FACT** (+2,812 GT) |
| V1 + all Phase 1/1b passes (v1_plus_all) | 90.08% | 0.9581 | **MEASURED FACT** (+7,722 GT) |
| V1 + all passes + uncapped (recovering 22,059 V1 + 6,983 pass quota losses) | ~94–95% est. | ~0.978 est. | **DIAGNOSTIC CEILING** (Phase 2 target) |
| V1 + all above + unconstrained collective sister (37,866 upper bound) | ~96–97% est. | ~0.988 est. | **DIAGNOSTIC CEILING** |
| V1 + all above + transliteration (4,854 script misses) | ~97–98% est. | ~0.993 est. | **DIAGNOSTIC CEILING** (Phase 3 target) |

> [!WARNING]
> The "unconstrained collective sister" (37,866) and "transliteration" (4,854) ceilings are
> theoretical upper bounds from labelled opportunity counts, NOT operational realities.
> When tested under strict inference safety, multi-seed sister expansion recovered 2,812 links.
> To reach the ~97% recall gate, the system MUST eliminate rank/cap losses (22k links)
> and bridge cross-script entities (4.8k links).

---

## D. MATCHER CEILING ANALYSIS

### D.1 Current matcher performance on recovered pairs

| Metric | Value |
|:---|---:|
| Recovered-truth macro F₀.₅ | 0.9448 |
| Pair precision | 0.9709 |
| Pair recall (of recovered) | 91.4% (306,775 / 335,657) |
| Matcher FN | 28,882 |
| Matcher FP | 9,212 |

### D.2 Feature ablation evidence

| Feature set | Features | Macro F₀.₅ | Δ from all_v1 |
|:---|---:|---:|---:|
| exact_provenance only | 16 | 0.722 | −0.161 |
| + token overlap | 28 | 0.832 | −0.051 |
| + numeric/address | 49 | 0.853 | −0.029 |
| + missingness/script | 53 | 0.858 | −0.025 |
| **all_v1** (+ RapidFuzz) | **61** | **0.883** | **baseline** |
| without rank/provenance | 52 | 0.881 | −0.002 |
| without address features | 33 | 0.749 | −0.134 |

RapidFuzz adds +0.025 F₀.₅ over `all_non_fuzzy`. Address features contribute +0.134.
Rank/provenance contributes only +0.002 — it's near-redundant given other features.

### D.3 Where are matcher errors concentrated?

| Error pattern | Count | % of total FN | Root cause |
|:---|---:|---:|:---|
| Address missing pairs | 7,762 | 26.9% | Insufficient evidence without address |
| Script conflict pairs | 3,969 | 13.7% | Cross-script name comparison fails |
| Rank 11+ candidates | ~5,000 | ~17.3% | Weak signal at tail of candidate list |
| Rank 0 candidates | 6,090 | 21.1% | Hard negatives that look like matches |

### D.4 Missing feature families that could help

| Feature family | Current status | Expected impact | Evidence |
|:---|:---|:---|:---|
| Character n-gram similarity | NOT in V1 | Moderate | V1 only has token-level Jaccard |
| Edit distance (Levenshtein) | NOT in V1 | Moderate | Only RapidFuzz ratio; no raw edit distance |
| Jaro-Winkler | NOT in V1 | Low-moderate | Covered partially by RapidFuzz |
| Cross-source sister evidence | NOT in V1 | **HIGH** | 37,866/41,956 misses have a sister |
| Provenance from new passes | NOT in V1 | Moderate | New passes create new signals |
| Score calibration/probability | NOT in V1 | Moderate | Raw LightGBM scores, no Platt/isotonic |
| Entity-level features | NOT in V1 | **HIGH** | No per-S1 aggregation features |

### D.5 Matcher headroom assessment

**MEASURED FACT**: With current features, the model has a learning-curve plateau (Tier B → C
gives only +0.0009 F₀.₅ from 2.5× more data). This means the 61-feature representation
is near-saturated for the current training configuration.

**HYPOTHESIS**: Adding cross-source evidence, entity-level features, and better character-level
similarity could provide ~2–4 pp improvement on recovered-truth F₀.₅.

**PROPOSED EXPERIMENT**: Feature engineering experiments on v2_candidate_research before
opening v2_matcher_tune.

---

## E. COLLECTIVE / GRAPH ENTITY RESOLUTION HYPOTHESIS

### E.1 The sister opportunity

| Diagnostic metric | Count/Value |
|:---|---:|
| Missed links with a retrieved true sister | 37,866 / 41,956 (90.3%) |
| Cross-source sister opportunities | 32,072 |
| Sister closer to S1 than S1 itself | 10,977 |
| Mean intra-set target name similarity | 0.718 |
| Mean intra-set target address similarity | 0.767 |
| Cross-source entity sets | 80,235 / 94,432 (85.0%) |

### E.2 Conceptual pipeline

```
Phase 1: Score all V1 candidates with frozen matcher
Phase 2: Identify high-confidence seeds (e.g., P(match) > 0.95)
Phase 3: For each seed target, find similar targets in opposite source
          using name/address token overlap (NOT GT-derived)
Phase 4: Add discovered targets as new candidates for the same S1
Phase 5: Re-score expanded candidate set
Phase 6: Apply decision policy
```

### E.3 Error propagation analysis

```
RISK: false seed → false neighbour → false candidate → false match

Safeguards required:
1. High-confidence seed threshold (e.g., P > 0.95, multiple pass support)
2. Opposite-source only (S2 seed → S3 candidates, and vice versa)
3. Minimum target-target name similarity (e.g., ≥ 0.7)
4. Maximum expansion quota (e.g., 10 per seed)
5. No iterative/recursive propagation (one hop only)
6. Cross-source requirement (no same-source expansion)
7. Frozen-model rescoring (no model contamination)
```

### E.4 Feasibility assessment

**MEASURED (Single-seed Phase 1a)**: Strict one-seed, one-hop, opposite-source, name-4-gram key, 10-candidate quota recovered **1,713** new GT links from 541,480 candidates (6.01 GT / 1k added).

**MEASURED (Multi-seed top-3 Phase 1b)**: Label-free multi-seed sister expansion (using global top-3 seeds selected by frozen V1 rank and IDF, rare 4-grams DF ≤ 1,000, opposite-source bridging, max 15 candidates per S1) recovered **2,812** new GT links from 971,676 standalone candidates (+565,952 candidates in union with V1, 4.97 GT / 1k added).

**EMPIRICAL REALITY CHECK**: Multi-seed expansion yielded a +64% increase in net GT recovery over single-seed (2,812 vs 1,713) and sits on the non-dominated Pareto frontier (`v1_plus_multiseed_sister`). However, 2,812 is significantly below the optimistic diagnostic ceiling of 10,000–15,000 (and far below the 37,866 unconstrained opportunity count).
Why did it plateau at 2.8k?
1. **Key eligibility & DF pruning**: Strict DF caps (DF ≤ 1,000) and rare 4-gram requirement prevent common or noisy token bridging.
2. **Quota truncation**: Over 16,856 candidate joins were truncated by the 15-candidate quota.
3. **One-hop limit**: If neither S2 nor S3 was retrieved in the top-3 seeds, one-hop bridging cannot reach the cluster.
4. **False propagation risk**: Unconstrained expansion beyond top-3 seeds or relaxing DF caps triggers candidate volume explosion without proportional GT yield.

**HISTORICAL PHASE 1 CONCLUSION**: Retrieval-seeded sister expansion contributed +2,812 GT links at 4.97 GT/1k within the tested DF and quota limits. That experiment did not test matcher-accepted seeds. The matcher-seeded one-hop grid is measured separately in the Phase 2 addendum; no general ceiling for collective methods follows from the Phase 1 result.

---

## F. MULTI-MATCH DECISION ANALYSIS

### F.1 V1 prediction distribution vs truth

| True match count | S1 | V1 Oracle F₀.₅ | V1 Matcher F₀.₅ | Gap |
|:---|---:|---:|---:|---:|
| 0 (singletons) | 6,332 | 1.000 | 0.894 | 0.106 |
| 1 | 6,020 | 0.878 | 0.772 | 0.106 |
| 2 | 18,607 | 0.935 | 0.872 | 0.063 |
| 3–4 | 50,581 | 0.951 | 0.905 | 0.046 |
| 5+ | 28,801 | 0.960 | 0.922 | 0.038 |

### F.2 Systematic underprediction

| Metric | Value | Implication |
|:---|---:|:---|
| Multi-match underprediction S1 | 44,738 | 43% of multi-match S1 have fewer predictions than truth |
| Multi-match overprediction S1 | 4,337 | 4.2% overpredict |
| Underprediction ratio | 10.3:1 | System is heavily biased toward under-matching |

The independent pair threshold (0.61) creates systematic underprediction because it treats
each pair independently. For an S1 with 5 true matches, even 90% per-pair recall means
a 41% chance of missing at least one (1 − 0.9⁵ = 0.41).

### F.3 Singleton false positives

672 singleton S1s received at least one prediction. This is 10.6% of singletons.
The per-S1 singleton accuracy is 89.4%.

For a 0.99 system, singleton FP must drop to ~3% or less (~190 S1s).

### F.4 Potential decision-policy improvements

| Method | Expected benefit | Complexity | Risk |
|:---|:---|:---|:---|
| Score calibration (Platt/isotonic) | Better threshold selection | Low | Low |
| Per-S1 score distribution features | Detect ambiguity/separation | Moderate | Low |
| Adaptive threshold by S1 evidence | Reduce underprediction | Moderate | Moderate |
| Source-balanced match selection | More complete sets | Low | Low |
| Minimum gap rejection | Reject near-tie singletons | Low | Low |

---

## G. OPEN-SET FRANCE RISK ANALYSIS

### G.1 Known facts

- France is test-only. No training labels exist.
- France S1 entities exist in the test set alongside India/US.
- The V1 model is trained on India + US only.
- The V1 candidate policy is country-partitioned (India, US hash buckets).
- `country_agreement` is constant=1 in training → the feature carries no information.

### G.2 Country-dependent components

| Component | Country-dependent? | Risk |
|:---|:---|:---|
| Candidate partitioning | YES — hash buckets are country-scoped | **If France partition missing, zero candidates** |
| DF thresholds | Same DF=2000 across countries | Low |
| Feature computation | Country-independent | Low |
| Normalization | Unicode NFKC, language-agnostic | Low |
| Suffix dictionary | English-biased (Ltd, LLC, Corp) | **Moderate** — French suffixes (SAS, SARL) may not be stripped |
| Matcher model | Trained on India/US distributions | **Moderate** — feature distributions may differ |

### G.3 Critical risk: Candidate partitioning

The V1 test candidate generation already produced 273M candidates with France included.
The production `materialize.py` handles France via a catch-all country partition. This is
verified in the test candidate audit.

### G.4 Recommended stress tests (training-data only)

1. **Leave-one-country-out**: Train on India only, evaluate on US. Measure degradation.
2. **Suffix sensitivity**: Run V1 pipeline on US data with French suffixes injected.
3. **Feature distribution comparison**: Compare feature quantiles between India and US.
   If distributions are similar, France generalization is more plausible.
4. **Normalization stress**: Test NFKC normalization on French diacritics (é, è, ç, ë).

---

## H. CANDIDATE RESEARCH ROADMAP (RANKED)

### Ranking criteria

| Criterion | Weight | Rationale |
|:---|:---|:---|
| Evidence of headroom | Highest | Must have measured or diagnostic ceiling |
| Precision risk | High | F₀.₅ is precision-heavy |
| Candidate explosion risk | High | Must scale to 1.7M test S1 |
| Inference feasibility | High | Must work without GT |
| Engineering complexity | Moderate | Budget is finite |

### Ranked research sequence

| Priority | Method | Expected recall gain | Candidate cost | Precision risk | Status |
|---:|:---|:---|:---|:---|:---|
| **1** | **Collective sister expansion** (multi-seed, inference-scored) | +3–8 pp pair recall | +2–5M pairs | Moderate (controllable) | Phase-1 tested at 1 seed; needs multi-seed |
| **2** | **Dynamic K / adaptive cap** (raise heavy-cap, per-S1 scoring) | +2–4 pp pair recall | +0 new keys, +caps | Low | Diagnostic only; 22,059 eligible losses |
| **3** | **Relaxed name/address 4-gram quotas** | +1–2 pp pair recall | +3–5M pairs | Low | Already tested with strict quotas |
| **4** | **Cross-source bridging** (target-target name similarity) | +1–3 pp | +1–3M pairs | Moderate | Diagnostic evidence exists (80,235 cross-source sets) |
| **5** | **Transliteration** (Devanagari↔Latin normalization) | +0.5–1.4 pp | +1–3M pairs | Moderate | 4,854 missed links; not yet tested |
| **6** | **Learned candidate ranking** (replace static quotas) | +1–2 pp | Revenue-neutral | Moderate | Requires model; deferred |
| **7** | **Character 2/3-gram retrieval** | <1 pp incremental | +10–50M pairs | HIGH | DF explosion; not recommended |

---

## I. MATCHER RESEARCH ROADMAP

| Priority | Method | Expected F₀.₅ gain | When to attempt |
|---:|:---|:---|:---|
| **1** | Address-missing feature engineering | +0.5–1 pp | After retrieval plateau |
| **2** | Cross-source sister evidence features | +1–2 pp | After collective expansion |
| **3** | Score calibration (Platt/isotonic) | +0.3–0.5 pp | After feature freeze |
| **4** | Entity-level aggregation features | +0.5–1 pp | After retrieval freeze |
| **5** | Character n-gram similarity | +0.2–0.5 pp | After more impactful features |
| **6** | Model architecture (deeper LightGBM / XGBoost) | +0.1–0.3 pp | After feature freeze |

---

## J. FEASIBILITY GATES

### HISTORICAL PLANNING QUESTION A: Could candidate oracle approach ~0.993?

**Current**: 0.9496 (V1) → 0.9574 (V2 Phase-1 all).  
**Planning heuristic**: ~0.993 under the assumed loss budget; not a mathematical requirement.  
**Gap**: 0.036.  
**Historical assessment**: UNPROVEN. The 37,866 sister opportunities were diagnostic, not
an operational recovery ceiling. Retrieval-seeded and matcher-seeded expansion require
separate measurement.  
**Current research gate**: End-to-end macro F₀.₅ ≥ research V1 + 0.02, paired CI lower bound
above zero, projected ≤400M test candidates, projected runtime ≤1.5× V1, and intact firewall.

### GATE B: Can the matcher recover most of the remaining oracle gap?

**Current**: Recovered-truth F₀.₅ = 0.9448. Oracle = 0.9496. Gap = 0.005.  
**Required**: Recovered-truth F₀.₅ ≥ 0.996.  
**Assessment**: PARTIALLY SUPPORTED. The current gap within recovered pairs is only 0.005,
but only the existing V1 61-feature configuration has a measured learning-curve plateau. New features (cross-source, entity-level)
are needed.  
**Test**: Feature engineering + model retrain on v2_matcher_train.  
**Go criterion**: Recovered-truth F₀.₅ ≥ 0.97 on v2_matcher_tune.  
**No-go criterion**: < 0.96 after exhausting feature ideas.

### GATE C: Can false-positive rate remain sufficiently low?

**Current**: Pair precision = 97.08%. Singleton accuracy = 89.39%.  
**Required**: Pair precision ≥ 99.0%. Singleton accuracy ≥ 97%.  
**Assessment**: PARTIALLY SUPPORTED. India pair precision is 95.8%, which is the weak point.  
**Risk**: Additional candidates from collective expansion may increase FP if seeds are unreliable.  
**Test**: Measure precision on v2_candidate_holdout.

### GATE D: Can multi-match behavior be controlled?

**Current**: 10.3:1 underprediction ratio.  
**Required**: ≤ 2:1.  
**Assessment**: HYPOTHESIS. Independent pair threshold is structurally biased.
An S1-level decision layer could address this.  
**Test**: Decision-policy experiments on v2_threshold.

### GATE E: Can open-set France remain robust?

**Assessment**: UNPROVEN. No France labels exist. Leave-one-country-out proxy testing is
the only available approach.  
**Test**: Train on India only, evaluate on US (and vice versa).

### GATE F: Can the resulting candidate set scale to full test?

**Current V1 test**: 273,502,145 candidates for 1,732,544 S1.  
**V2 projection at `v1_plus_all` density**: 185.28 × 1,732,544 ≈ **321M** candidates.  
**With collective expansion**: Could reach 350–400M.  
**Assessment**: LIKELY FEASIBLE. V1 already handles 273M.  
**Constraint**: Feature generation peak RSS was 1.2 GB; must fit in available memory.

---

## K. FULL-SCALE RESOURCE PROJECTIONS

### K.1 Candidate generation

| Configuration | Research (100k S1) | Projected (1.7M S1) | RSS | Runtime |
|:---|---:|---:|---:|---:|
| V1 baseline | 15.6M | 273M (actual) | 1.19 GB | 1,008s |
| V1 + all passes | 18.5M | ~321M | 1.19 GB | 1,448s |
| V1 + collective | ~20–22M est. | ~350–380M est. | ~1.5 GB est. | ~2,000s est. |

### K.2 Feature generation

| Candidates | Feature storage | RSS | Runtime |
|---:|---:|---:|---:|
| 17.3M (V1 final eval) | 583 MB | 1.2 GB | 1,253s |
| 273M (V1 test) | ~9.2 GB | ~1.2 GB | ~5.5 hrs |
| 350M (projected V2) | ~11.8 GB | ~1.5 GB | ~7 hrs |

### K.3 Scoring

| Candidates | RSS | Runtime |
|---:|---:|---:|
| 17.3M | 1.5 GB | 86s |
| 273M | ~1.5 GB | ~23 min |
| 350M | ~1.5 GB | ~30 min |

### K.4 Storage budget

| Artifact | Size per 1M candidates |
|:---|---:|
| Candidate pairs (Parquet) | ~8.5 MB |
| Features (Parquet) | ~33.7 MB |
| Scores (Parquet) | ~11.8 MB |
| Temporary (DuckDB) | ~20 MB |

For 350M candidates: ~1.7 GB candidates + ~11.8 GB features + ~4.1 GB scores = **~17.6 GB**
plus ~7 GB temporary. Feasible on a machine with 250 GB free disk.

---

## L. EXPLICIT EXPERIMENT LIST — WHAT TO DO NEXT

### PHASE 0 — Current V1 evidence ✅ COMPLETE
- V1 final eval metrics gathered.
- V2 Phase-1 miss decomposition complete.
- Initial Phase-1 individual retrieval passes tested.

### PHASE 1 — Collective expansion research ✅ COMPLETE (Phase 1a & 1b Materialized)
- **Population**: v2_candidate_research (100,000 S1) — strictly label-free seed generation.
- **Measured (Single-seed Phase 1a)**: 541,480 candidates (+285,051 in union); +1,713 net GT links recovered (6.01 GT / 1k added); pair recall 88.34%.
- **Measured (Multi-seed top-3 Phase 1b)**: 971,676 candidates (+565,952 in union); +2,812 net GT links recovered (4.97 GT / 1k added); pair recall 88.66%.
- **Combined with Phase 1 additions (`v1_plus_all`)**: 18,895,613 candidates (+3,246,152 in union); **+7,722 net GT links recovered**; pair recall **90.0814%** (+2.24 pp); oracle macro F₀.₅ **0.958075** (+0.0094).
- **Core finding**: This retrieval-seeded configuration was a non-dominated frontier addition, recovering 2,812 links within DF ≤ 1,000 and 15-candidate quota. Its measured yield does not bound other collective configurations.
- **Sealed data opened**: NONE.

### PHASE 2 — Historical rank/cap proposal (now measured in the Phase 2 addendum)
- **Population**: v2_candidate_research (100,000 S1)
- **Problem addressed**: 22,059 V1 truth misses had eligible keys but were killed by rank/cap. In addition, 6,983 eligible GT links were killed by quotas in the new Phase 1 passes (including 4,478 in `name_char4` alone).
- **Hypothesis**: Raising heavy sorted-name cap from 100 to 200–300, relaxing per-source quotas (from 50/50 to 80/80 or dynamic K), and expanding `name_char4` quota recovers ≥8,000 additional GT links.
- **Method**: Parameter sweep on `v2_candidate_research` blocking configs. Measure candidate volume explosion vs net GT link recovery.
- **Success criterion**: ≥+3 pp pair recall gain (reaching ~93–94% recall) with candidate volume increase ≤50% (under 28M candidates).
- **Failure criterion**: <1 pp gain OR >75% volume explosion (>32M candidates).
- **Sealed data opened**: NONE.

### PHASE 3 — Advanced retrieval (if justified by Phase 1–2)
- **Population**: v2_candidate_research
- **Hypothesis**: Transliteration and cross-source bridging recover ≥2,000 additional GT links
  not covered by Phases 1–2
- **Prerequisite**: Phase 1 oracle < 0.985 AND transliteration opportunity confirmed
- **Sealed data opened**: NONE

### PHASE 4 — Candidate policy holdout validation
- **Population**: v2_candidate_holdout (100,000 S1) — FIRST SEAL OPENED
- **Hypothesis**: Research-optimized candidate policy generalizes (oracle F₀.₅ ≥ 0.98)
- **Prerequisite**: Research oracle ≥ 0.975. All retrieval experiments completed.
- **This is a ONE-TIME evaluation. No parameter tuning after opening.**
- **Success criterion**: Holdout oracle ≥ 0.98 AND pair recall ≥ 95%
- **Failure criterion**: Holdout oracle < 0.975 OR pair recall < 93%

### PHASE 5 — Matcher improvement
- **Population**: v2_matcher_train (1,043,048 S1) for training; v2_matcher_tune (50,000) for selection
- **Hypothesis**: New features + retrained model achieves recovered-truth F₀.₅ ≥ 0.97
- **Prerequisite**: Candidate policy frozen after Phase 4
- **Features to add**: Cross-source evidence, entity-level aggregation, character similarity,
  score calibration
- **Sealed data opened**: v2_matcher_train, v2_matcher_tune

### PHASE 6 — Decision-policy calibration
- **Population**: v2_threshold (50,000 S1)
- **Hypothesis**: Optimized decision policy (adaptive threshold, margin-based, S1-level)
  achieves macro F₀.₅ ≥ 0.97
- **Prerequisite**: Matcher frozen after Phase 5
- **Sealed data opened**: v2_threshold

### PHASE 7 — Final evaluation
- **Population**: v2_final_eval (100,000 S1)
- **ONE-TIME evaluation. No changes after opening.**
- **Prerequisite**: Matcher + decision policy frozen after Phase 6
- **Success criterion**: Macro F₀.₅ ≥ 0.96 (conservative; 0.99 remains aspirational)
- **Sealed data opened**: v2_final_eval

### PHASE 8 — Test inference and submission
- **Population**: Full test set
- **Prerequisite**: One-time Phase 7 confirmation, frozen pipeline, and a human release decision
- **Actions**: Generate test candidates, features, scores, predictions, submission ZIP
- **No further parameter changes**

---

## M. EXPLICIT LIST — WHAT NOT TO DO

| Approach | Reason to avoid |
|:---|:---|
| Indiscriminate all-pairs similarity | Explodes to billions of pairs; infeasible |
| Unbounded fuzzy name/address joins | DF-unbounded keys create 10M+ noise candidates |
| Character 2-gram or 3-gram retrieval | Profiled at 738K–11.7M joins; DF explosion; minimal incremental recall |
| Large cross-encoder/transformer inference | 300M+ pair scoring at high GPU cost; not challenge-appropriate |
| GT-derived retrieval indexes | Using ground truth to build blocking keys is label leakage |
| Tuning to public leaderboard | Score feedback is not a valid optimization signal |
| Repeated holdout evaluation | Each sealed population should be opened at most ONCE |
| Broad hyperparameter sweeps on unchanged V1 61-feature configuration | The measured V1 learning curve shows little gain from more training rows; new feature/model families remain untested |
| Embeddings without demonstrated retrieval benefit | Adds complexity; no evidence of incremental value over token overlap |
| Uncontrolled graph propagation | Risk of false-positive chains; must be bounded and one-hop |
| Adding complexity without error-budget justification | Every change must target a measured loss source |
| Opening v2_matcher_train before candidate policy is frozen | Training on unfrozen candidates wastes the population |

---

## N. CONDITIONS BEFORE OPENING EACH SEALED POPULATION

| Population | Open when | Never open if |
|:---|:---|:---|
| **v2_candidate_holdout** | Research oracle ≥ 0.975 AND all retrieval experiments complete | Never if research oracle < 0.965 |
| **v2_matcher_train** | Candidate policy frozen after holdout validation | Never before holdout validation |
| **v2_matcher_tune** | Matcher trained on v2_matcher_train | Never before matcher training |
| **v2_threshold** | Matcher frozen after tune evaluation | Never before matcher freeze |
| **v2_final_eval** | Complete pipeline frozen (matcher + threshold + decision) | Never before pipeline freeze |
| **Test data** | After one-time final confirmation, pipeline freeze, and human release decision | Never before final eval |

---

## O. CONDITIONS BEFORE TOUCHING TEST

1. v2_final_eval has been opened and evaluated exactly once.
2. The one-time final-eval result has been reviewed by humans as confirmation of the frozen pipeline, with no iterative tuning from that result.
3. Candidate policy, feature spec, matcher model, threshold, and decision policy are all frozen.
4. All frozen artifacts are checksummed.
5. Test inference runbook has been verified on eval data.
6. Resource projections confirm the test run is feasible within available compute.

---

## P. CONDITIONS BEFORE MAKING ANOTHER SUBMISSION

1. All conditions in section O are met.
2. Test inference has completed successfully.
3. Output passes official validator.
4. `matching_results.tsv` and `candidate_pairs.tsv` are structurally audited.
5. Submission ZIP is created and checksummed.
6. No V1 artifacts have been modified.
7. V1 backup remains intact and independently verifiable.

---

## FINAL SUMMARY

### What we know (MEASURED FACTS):

1. **V1 retrieval bottleneck**: V1 loses 46,199 truth pairs at retrieval (12.1% of truth), capping the candidate oracle at 0.9496 (0.9487 on research).
2. **Retrieval miss decomposition**: Of the 41,956 research misses, 19,897 (47.4%) had no eligible V1 key, while 22,059 (52.6%) had a valid key but were eliminated by ranking/caps.
3. **Phase 1/1b materialized recovery**: Combining all new passes (`v1_plus_all`) added 3.25M candidates (+20.7%) and recovered **7,722 new GT links**, raising pair recall from 87.84% to **90.08%** and oracle macro F₀.₅ from 0.9487 to **0.9581**.
4. **Multi-seed sister expansion reality**: Multi-seed sister expansion (top-3 seeds, 15-quota) yielded **2,812 net GT links** at 4.97 GT / 1k added candidates. While +64% higher than single-seed (1,713), it fell far short of the 10,000+ diagnostic ceiling due to necessary DF caps and quota bounds.
5. **Quota bottleneck in new passes**: New passes also suffer heavy quota truncation: `name_char4` alone lost 4,478 incremental V1 misses to its 50-candidate quota; `sister_expansion` lost 1,779 to its quota.
6. **Current-configuration learning curve**: The V1 61-feature LightGBM setup plateaus near ~200k training rows in its measured learning curve; this says nothing conclusive about new features or models.
7. **Severe domain failure modes**: Address-missing pairs achieve only 72.5% pair recall (77.5% in `v1_plus_all`); Latin-to-Indic script conflict pairs achieve only 62.2%–69.9% recall.
8. **Decision underprediction**: True multi-match entities are underpredicted 10.3:1 (44,738 S1 underpredicted vs 4,337 overpredicted) due to a single global threshold (0.64).

### What we do not know (UNMEASURED QUANTITIES):

1. **Rank/cap headroom**: Exactly how many of the 22,059 V1 cap losses and 6,983 pass quota losses can be recovered by increasing caps (e.g., 100 → 250) before candidate volume explodes past manageable limits.
2. **Transliteration recovery**: Whether phoneme/Devanagari-Latin transliteration can operationalize the 4,854 script-conflict misses without generating massive phonetic cross-chatter in Indian cities.
3. **Feature discrimination headroom**: Whether second-stage graph features (e.g., sister-agreement score, entity-degree calibration) can lift recovered-pair macro F₀.₅ from 0.9448 to ≥0.97.
4. **Adaptive thresholding impact**: Whether entity-level dynamic thresholding (e.g., threshold as a function of top candidate score and gap) can eliminate the 44,738 multi-match underpredictions without increasing singleton false positives.
5. **Open-set France generalization**: How the candidate blocking and matcher will behave on France data (0 labeled training examples).
6. **Ultimate 0.99 feasibility**: Whether the aggregate ceiling across retrieval, matching, and decision can truly reach 0.990+ or whether inherent dataset label noise establishes a hard ceiling around ~0.94–0.96.

### The highest-value next experiment:

**UPDATED AFTER PHASE 2:** A human-approved, research-only matcher-feature ablation on newly retrieved true links and the address/script failure slices, after firewall review. The measured cap sweep recovered only 208–2,243 net links depending on configuration; the largest gains exceeded projected resource limits. This is a proposed next experiment, not an authorization to open sealed populations or train a model in this phase.

### What result would justify continuing toward 0.99+:

The earlier oracle-only planning target is superseded. The current human gate is measured research end-to-end macro F₀.₅ ≥ V1 research + 0.02, positive paired 95% CI lower bound, ≤400M projected test candidates, ≤1.5× V1 projected runtime, and intact firewall. Phase 2 did not satisfy it.

### What result would cause us to abandon the 0.99+ target:

If raising caps to 300 produces excessive volume and little end-to-end gain, that candidate-policy direction should be deprioritized. Such a result does not prove that 0.99+ is unreachable for all future feature, matcher, and retrieval approaches.

### What must remain sealed:

- **v2_candidate_holdout** — until all retrieval research (Phases 1, 2, 3) is finalized.
- **v2_matcher_train** — until candidate policy is formally frozen.
- **v2_matcher_tune** — until candidate policy is frozen and matcher model is trained.
- **v2_threshold** — until matcher features and model are frozen.
- **v2_final_eval** — until the entire end-to-end pipeline is frozen (single-use evaluation).
- **Test data** — until one-time final confirmation and a human release decision; final-eval results are not an iterative tuning signal.

---

## PHASE 2 MEASURED ADDENDUM — 2026-09-27

The source-context version of this plan is preserved exactly at `work/v2_phase2_r1/strategic_plan_context_original.md`, SHA-256 `FED8357C75E277A508FBFC57E1D460E0D07421E4355C553B53B20CB513F6CBAE`. The comprehensive measured report is `work/v2_phase2_r1/report.md`; its companion `artifact_inventory.json` lists per-file SHA-256. Historical V1 and Phase 1/1b results above remain historical evidence. The earlier retrieval-seeded multi-seed sister experiment and this matcher-seeded expansion use different seed policies.

### Frozen-matcher end-to-end evidence on 100,000 research S1s

| Candidate policy | Research pairs | Pair recall | Candidate oracle F0.5 | Frozen V1 end-to-end macro F0.5 | Paired delta vs `v1_plus_all` |
|---|---:|---:|---:|---:|---:|
| V1 | 15,649,461 | 0.878441 | 0.948700 | 0.895261 | — |
| `v1_plus_all` | 18,895,613 | 0.900814 | 0.958075 | 0.901783 | baseline |
| Best matcher-seeded one-hop, seed 0.61 / quota 20 / DF 5,000 | 21,432,764 | 0.907536 | 0.959969 | 0.902748 | +0.000966 |
| Best tested cap combination, heavy 300 / source 80 / name4 80 | 28,338,439 | 0.907313 | 0.961103 | 0.903551 | +0.001768 |

The Step 0 paired 1,000-resample S1 bootstrap for `v1_plus_all − V1` is +0.006522, 95% CI [0.006160, 0.006858]. The best matcher-seeded one-hop point has a positive paired delta of +0.000966 vs `v1_plus_all`, 95% CI [0.000882, 0.001052]. It recovered 2,320 GT links beyond `v1_plus_all`, of which the frozen matcher accepted 1,222 while introducing 86 final false positives. Its 2,537,151 genuinely new pairs yielded only 0.914 net GT links per 1,000. This establishes a small measured end-to-end benefit for the tested one-hop grid, with no general conclusion about all collective approaches. Lower research-only second-stage thresholds 0.30 and 0.45 were separately evaluated and remain optimistic tuning results.

The `v1_plus_all` baseline accepted 5,149 of the 7,722 newly retrieved GT links. The 37,866 retrieved-sister opportunities remain a diagnostic count, not a recoverable operational ceiling. After `v1_plus_all`, 34,234 GT links remain unretrieved, including 16,569 from the historical V1 rank/cap miss category after deduplication. The cap sweep measured 208–2,243 net GT links beyond `v1_plus_all`; its strongest combined setting is projected at 490,975,925 test candidates and 1.868× the measured V1 test-stage runtime envelope, exceeding both hard resource limits. The strongest cap setting within the projected limits is name4 quota 80: macro F0.5 0.901953, paired delta +0.000170, positive CI lower bound, projected 347,009,904 test candidates and 1.342× runtime.

The best matcher-seeded one-hop point projects to 371,332,067 test candidates and 1.429× V1 runtime after measured index/grid costs. These are density-based estimates using historical aggregate V1 test receipts; no test rows were accessed, and V2 peak RSS remains unverified. Two-hop was not run because the DF-5,000 one-hop index already required 1.021B estimated joins and 23 minutes, while a bounded second-hop index over further targets was not preflighted.

### Gate and firewall status

The primary human gate target is 0.915261 research macro F0.5 (V1 + 0.02). The best tested end-to-end result is 0.903551, below that target; the best resource-conforming one-hop result is 0.902748. An initial Phase 2 guard decoded sealed-population row-level membership IDs once, without reading labels. Subsequent Phase 2 scripts use research-only membership. This is an access-boundary violation under the strict zero sealed-population access rule and independently prevents gate eligibility. The exact event is recorded in `work/v2_access_ledger.jsonl` as `phase2_initial_guard_membership_decode`. No sealed labels or real test rows were read. `v2_candidate_holdout`, `v2_matcher_train`, `v2_matcher_tune`, `v2_threshold`, and `v2_final_eval` remain label-sealed. Final evaluation remains one-time confirmation after pipeline freeze, never an iterative tuning signal.

The evidence-backed next proposal is a research-only matcher-feature ablation focused on newly retrieved but rejected true links, address-missing pairs, and script conflicts, subject to a human decision and renewed access review. This is a hypothesis, not Phase 2 implementation. The result does not establish that 0.99+ is achievable or impossible.

---

> [!CAUTION]
> This plan does NOT guarantee 0.99+. It identifies the experiments that could
> establish whether 0.99+ is achievable. The actual outcome depends on unmeasured
> quantities. Intellectual honesty requires stating: **the gap between 0.896 and 0.991
> is large, and closing it will require simultaneous improvements across retrieval,
> matching, and decision-making.**
