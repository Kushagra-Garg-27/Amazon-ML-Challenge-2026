# V3 maximum-score and final submission sprint

User brief: attachment `80bf3935-3e58-43ff-9c74-5d47d84bae54/Pasted text.txt`.
New research outputs belong in `work/v3_research_r1`; preserve all V1 and V2
results. The final destination is the requested validated broCode release, not
completion of training alone. No score near 0.99 is promised.

## Recovery and scope

1. Preserve the interrupted capacity experiment and completed results; inspect
   all receipts and append-only ledger rows. After execution resumes, test recent
   score-export and manifest-binding fixes. Do not restart capacity 255 by default.
2. Record provenance separately: 0.920450 is role1 model selection; 0.922415 is
   odd role2 policy selection; 0.923448 is odd role2 residual policy selection.
   The residual model fits role1 and early-stops on even role2. These are exposed
   development estimates. Role3 is internal research assessment, not a sealed
   challenge holdout; prior aggregate research exposure must remain disclosed.
3. Evaluate the fully fixed selected policy once on role3, reproduce raw scores,
   corrected decision scores and decisions, and freeze the portable inference
   artifacts in `artifacts/final_candidate_matcher_v2/`. Generalize materialization
   to arbitrary audited candidate sets: recompute candidate-context/anchors from
   that set, while preserving all frozen feature definitions and model bytes.
4. Explicitly justify four historical label-reading test skips. Run all other
   tests; never reopen their historical labels merely to remove skips.

## Firewall

Use only positive membership authorization against the approved 100k research
population. The legacy `runtime.populations()` reader was retired with an
unconditional pre-I/O exception; old scripts using it must not run. Its historical
sealed-ID decoding incident remains in the audit. The new synthetic regression
must prove no read occurs. No sealed membership, sealed labels, external enrichment
or test labels are authorized. Observable test attributes become relevant only
after architecture freeze and test-scale preflight, per the V3 brief.

## Retrieval research

Recompute current-union misses on the authorized development cohort. The historic
19,897 count is missed GT PAIRS with no eligible V1 key, not distinct S1s. Report
both pair and unique-S1 counts; distinguish absence of an eligible key for a
particular true pair from S1 having no observable blocking keys at all.

Classify verified eligibility/DF/rank losses separately from overlapping name,
address, acronym, script, numeric, formatting and target-sister signals. DBA/trade
name categories require actual observable evidence; unknown cases stay unknown.
Label-derived diagnostic categories must never select inference query subsets.

Prioritize bounded target-sister and transliteration/name/address passes using
measured remaining-miss opportunities. Reuse existing indexes and rejected-pass
evidence. Record complete target DF provenance; never build a GT-filtered index.
Measure each pass's pair/unique-S1 recovery, duplicate overlap, candidate cost,
tail counts, runtime, memory and projected full-test cost before unions. Freeze
the matcher while comparing unions and adaptive budgets. Retain measured >=0.001
end-to-end gains, or explicitly demonstrated prerequisites. Explore bounded
two-hop expansion only after one-hop evidence supports it.

## Final validation boundary

V2 assessment is a baseline evaluation, not a tuning cohort for V3. V3 retrieval
design must use role0/1/2 only; do not use role3 errors or per-row outcomes. Freeze
V3 before one role3 comparison, disclose its earlier baseline exposure and the
historical full-research aggregate exposure. This is internal research validation,
not an untouched challenge evaluation. Do not open a sealed population to improve
that claim without explicit authorization.

## Test inference and release

Before large runs print projected rows/bytes, partition count, time, RAM and spill
requirements. Freeze retrieval/model/features/calibration/decision first. Then
preflight test attributes, generate resumable partitioned candidates/features/
scores with receipts, and stream one output row per 1,732,544 test S1s. Candidate
TSV must encode exactly the matcher input set; matches must be contained in it.

Confirm challenge-required archive contents from local official instructions;
the existing V1 packager has frozen V1 assumptions and is not a V3 packager.
Preserve the previous release before replacing requested final paths. Validate
both original and freshly extracted outputs with `utils/validate_submission.py`.
Its default skips target-ID existence checks, so also perform bounded exact ID
joins (or the official `--check-ids` mode if resource-safe). Verify row universe,
duplicates, candidate identity, containment, empty rows and deterministic order.
Package an explicit required-file allowlist, never the workspace. Record every
requested SHA/count, code commit, command and validator status in the release
manifest. No new ZIP is a completed release until both validator runs succeed.

## Current execution constraint

Automatic approval review refused a new Python test run because account usage
capacity was exhausted (message gave reset September 28, 03:33). This was a review
failure, not an unsafe-action ruling. Do not bypass by changing executable, shell
or process launcher. The user subsequently restricted work to preparation and
auditing only; the running process was stopped. No Python, tests, assessment,
packaging or inference may resume until capacity is restored and the user resumes
execution. Latest source changes remain unverified.
