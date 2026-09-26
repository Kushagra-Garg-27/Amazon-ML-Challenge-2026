# Frozen candidate policy: evidence source 50/50 + heavy cap 100

Policy ID: `evidence_source_50_50_heavy100_v1`

## Candidate identity and retrieval

Candidate identity is `(source1_entity_id, target_entity_id)`. Retrieval unions four label-free passes:

1. exact frozen sorted name (`provenance=1`);
2. exact frozen normalized address (`provenance=2`, uncapped);
3. shared normalized name token with target DF at most 2,000 (`provenance=4`);
4. shared normalized address token with target DF at most 2,000 (`provenance=8`).

Target DF is the count of distinct S2/S3 target entities for each `(country, token)`, calculated separately for the name and address fields. Provenance is combined with bitwise OR after unioning retrieval passes and before pruning.

## Evidence ranking

For each token pass, `score = sum(1 / target_df(token))` over distinct shared eligible tokens. `shared_token_count` is the number of those tokens. The other fields are:

- `coverage_s1 = shared_token_count / distinct S1 tokens`;
- `coverage_target = shared_token_count / distinct target tokens`;
- `token_jaccard = shared / (S1 tokens + target tokens - shared)`;
- `exact_name` and `exact_addr`: equality of non-empty frozen normalized fields;
- `postal_shared`: equal non-empty first 5–6 digit address sequence;
- `numeric_shared`: equal non-empty first numeric address sequence;
- `both_pass`: present in both the name-token and address-token top-200-per-source shortlists.

The exact lexicographic order is:

`both_pass DESC, postal_shared DESC, numeric_shared DESC, exact_name DESC, exact_addr DESC, round(score,12) DESC, shared_token_count DESC, coverage_s1 DESC, coverage_target DESC, token_jaccard DESC, target_entity_id ASC`.

False is used when a required non-empty exact/numeric value is absent. Coverage or Jaccard is null only for a zero denominator; descending nulls sort last. The final target ID order is stable and ascending. Source is excluded from identity evidence and is used only for per-source ranks and quotas.

No GT label, fitted validation coefficient, test label, or external identity data enters ranking. Every input is available during test inference.

## Pruning and output

- retain up to rank 50 from S2 and rank 50 from S3 in each name-token and address-token pass;
- for exact sorted-name blocks with at least 120 target records, rank with `exact_addr DESC, postal_shared DESC, numeric_shared DESC, exact_name DESC, address_jaccard DESC, target_entity_id ASC` and cap sorted-name-only rows at 100;
- exact-address candidates and rows with any additional provenance remain uncapped by the heavy-name rule;
- deduplicate on candidate identity and union provenance;
- write 16 `hash(source1_entity_id)` partitions, each ordered by candidate identity.

## Materialized operating point

- candidates: 34,568,979;
- recovered validation GT links: 670,785 / 763,919;
- pair recall: 0.878083933;
- GT-bearing S1 coverage: 0.979198479;
- mean / p95 / p99 / max candidates per S1: 156.75 / 201 / 213 / 292.

The production module reproduced the experimental artifact with 0 added candidates, 0 removed candidates, and 0 duplicate identities.

## Reproduce and verify

```powershell
$env:PYTHONPATH='code/business_entity_resolution/src'
.venv\Scripts\python.exe scripts/measure_peak.py --module er.candidates.materialize -- --config work/final_candidate_policy.json --rank-root work/freeze_gate --output-dir work/final_candidate_policy_parts --no-resume
.venv\Scripts\python.exe scripts/measure_peak.py --module er.candidates.audit -- --reference work/freeze_gate/evidence_source_50_50_heavy100.parquet --parts work/final_candidate_policy_parts --output work/final_candidate_artifact_manifest.json
```

The future matcher must score exactly the post-pruning identities in `work/final_candidate_policy_parts`.
