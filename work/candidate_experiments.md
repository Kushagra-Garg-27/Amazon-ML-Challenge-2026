# Candidate-generation experiments — DEVELOPMENT split (work/split_s1.parquet, val)

Base: val S1=220531; val S1 with >=1 GT link=208254; total GT links=763919. PRIMARY metric = pair-level GT-link recall. S1-coverage reported separately.

## Step 2 — single-key baselines (exact volume via key products)

  - exact_name: pair-recall=0.4702 (359166), S1-coverage=0.8294 (172723), distinct-cands=8555794, cand/S1 mean=38.80 median=3 p90=78 p95=167 p99=650 max=1424, zero-cand-S1=18791
  - sorted_name: pair-recall=0.5061 (386646), S1-coverage=0.8562 (178305), distinct-cands=9060998, cand/S1 mean=41.09 median=4 p90=81 p95=180 p99=670 max=1437, zero-cand-S1=15238
  - exact_addr: pair-recall=0.0832 (63580), S1-coverage=0.2378 (49513), distinct-cands=77772, cand/S1 mean=0.35 median=0 p90=1 p95=2 p99=3 max=20, zero-cand-S1=166958

## Step 3 — exact-name ⊆ sorted-name containment (verified, not assumed)

  (a) GT pairs exact-covered but NOT sorted-covered = 0 (0 expected)
  (b) target (cc,name_nosuffix) groups with >1 distinct name_sorted = 0 (0 expected)
  exact-name recall=0.4702 (359166) ⊆ sorted-name recall=0.5061 (386646); incremental GT links sorted-over-exact = 27480. CONTAINMENT HOLDS ⇒ exact-name subsumed; excluded from the final union.

## Step 5 — token frequency profiles (justify drop thresholds)

  nametok: target (cc,tok) keys=1483743, target-DF median=1 p90=4 p95=15 p99=142 max=296635
    top-DF tokens (generic → dropped): india:लिमिटेड(296635), us:center(292790), us:partners(267771), us:com(266460), india:प्राइवेट(246472), us:s(229948), india:india(229501), us:c(225988)
  addrtok: target (cc,tok) keys=778617, target-DF median=4 p90=32 p95=93 p99=822 max=1827202
    top-DF tokens (generic → dropped): india:no(1827202), india:road(767228), us:st(639328), us:rd(599717), india:floor(584256), us:street(565999), india:nagar(554778), us:dr(544979)

## Steps 4/6 — token passes (frequency-aware, block-capped)

  nametok (drop target-DF>2000): surviving tokens=1482336, dropped=1407; pre-dedup join estimate=127814261
    per-S1 top-100: distinct candidates=11993715, realized (post-cap) pair-recall=0.3606 (275476) vs uncapped ceiling 0.5317; cand/S1 mean=54.39 median=96 p90=100 p95=100 p99=100 max=100, zero-cand-S1=86517
  addrtok (drop target-DF>2000): surviving tokens=775125, dropped=3492; pre-dedup join estimate=247262860
    per-S1 top-100: distinct candidates=18920903, realized (post-cap) pair-recall=0.5922 (452395) vs uncapped ceiling 0.7965; cand/S1 mean=85.80 median=100 p90=100 p95=100 p99=100 max=100, zero-cand-S1=19747

## Step 6 — cumulative union pair-recall (fixed order) + incremental GT links

  + exact_name: union pair-recall=0.4702 (359166); incremental GT links=359166; union S1-coverage=0.8294
  + sorted_name: union pair-recall=0.5061 (386646); incremental GT links=27480; union S1-coverage=0.8562
  + exact_addr: union pair-recall=0.5577 (426013); incremental GT links=39367; union S1-coverage=0.8860
  + nametok: union pair-recall=0.7338 (560572); incremental GT links=134559; union S1-coverage=0.9308
  + addrtok: union pair-recall=0.9425 (719960); incremental GT links=159388; union S1-coverage=0.9900

## Step 8 — per-country + address-missingness (union of all passes)

### by country (open-set; NOT hard-coded)
  - us: union pair-recall=0.9550 (438269/458902)
  - india: union pair-recall=0.9235 (281691/305017)
### by address presence
  - both present: union recall=0.9480 (692502/730466); of which address-pass-covered=613689
  - either missing: union recall=0.8208 (27458/33453); of which address-pass-covered=0

## Step 7 — address similarity → retrieval conversion (NOT the same thing)

  - lost-by-exact pairs with both addresses = 389984
  - of those, addr Jaccard>=0.5 (SIMILARITY OPPORTUNITY) = 281248
  - ACTUALLY retrieved by exact-address pass = 41134 (14.6% of the opportunity)
  - ACTUALLY retrieved by exact-addr OR addr-token = 252320 (89.7% of the opportunity)
  - similar (>=0.5) but NOT retrieved by any address pass = 28928 → similarity does NOT imply retrieval

## Step 11 — final candidate stream (deduped identity + provenance bitmask)

  passes materialized into final stream: ['cand_sorted', 'cand_addr', 'cand_nametok', 'cand_addrtok'] (prov bits: sorted=1, addr=2, nametok=4, addrtok=8)
  per-pass-capped union distinct candidates=39241986 (nametok/addrtok already top-100/S1)
  UNCAPPED retrieval ceiling (pk oracle, union of passes)=0.9425 (719960)
  per-pass-capped union pair-recall=0.8614 (658063); pruning loss from the per-pass top-100 cap = 61897
  cand/S1 mean=177.94 median=188 p90=206 p95=272 p99=770 max=1538

## Step 9 — pruning/capping (per-pass-capped union vs added union-level top-K)

  retrieval ceiling (no cap) = 0.9425; losses below are attributable to capping only.
  - per-pass-capped union (top-100/pass, no union cap): candidates=39241986, pair-recall=0.8614 (658063); cumulative pruning loss vs ceiling=61897
  - + union cap top-100/S1: candidates=21216583, pair-recall=0.6269 (478907); cumulative pruning loss vs ceiling=241053
  - + union cap top-50/S1: candidates=10768368, pair-recall=0.5619 (429271); cumulative pruning loss vs ceiling=290689
  final stream + provenance → work/final_candidate_provenance.parquet

## Step 14 — remaining lost pairs (selected union: sorted∪addr∪nametok∪addrtok)

  remaining lost GT links = 43959 (of 763919; union recall=0.9425)
  - missing address (either side): 5995 (13.6% of lost)
  - cross-script name (deva↔latin): 7408 (16.9% of lost)
  - zero name-token overlap: 21512 (48.9% of lost)
  - low name overlap (0,0.5): 7782 (17.7% of lost)
  - both addr present, addr jac<0.5: 21201 (48.2% of lost)

## Step 15 — full-scale feasibility (estimate; not run at full scale)

  sorted-key block self-product over FULL train targets (S2∪S3) ≈ 264059919 (worst-case within-target block cost proxy; actual S1×target scales with test S1 count).
  Development val (220,531 S1) ran within the 512MB DuckDB cap, spilling to work/duckdb_tmp; test S1=1,732,544 (~7.9× val). Recommended: partition candidate generation by country_norm and stream per-block to disk (integer-coded IDs); the per-block cap keeps peak RSS bounded. No unsafe all-pairs run performed.

## Step 12 — grouped name-key stress-test (SELECTED policy, run ONCE, diagnostic)

  grouped val GT links=773805; SELECTED-policy union pair-recall=0.9416 (728594)
  INTERPRETATION: distribution-shift diagnostic on unseen exact name-keys. NOT a guaranteed lower bound, NOT a leaderboard/private-score/ranking estimate.
