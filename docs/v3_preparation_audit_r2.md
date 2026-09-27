# V3 preparation audit, revision 2

Status: source preparation only; Python and tests were not executed.

Ruling: the user's preparation-only instruction overrides normal execution and
test-first verification steps while capacity is unavailable. Synthetic tests were
written before these edits, but no RED/GREEN result or runtime correctness is claimed.
The existing experiment ledger and validated artifacts are not preparation logs.

## Findings addressed in draft source

- Null matcher IDs could be omitted by MD5 partition predicates. The prepared
  output auditor now validates IDs and source membership globally, validates final
  acceptance/score values, and reconciles partition totals with full input counts.
- A bare project-test PASS flag did not bind the packaged code. The prepared
  packager now requires a hashed receipt with test execution records and source
  hashes, and compares packaged Python/requirements with the named Git commit.
- Model/policy hash strings were not tied to audited inference or packaged files.
  They now require matching archive entries and the audited inference manifest.
- Windows path aliases and case collisions could overwrite extracted entries.
  Prepared checks reject normalized aliases, reserved devices, invalid characters,
  case collisions and file/directory collisions.
- Prepared artifacts could change during an audit/copy. Inputs are rehashed before
  success receipts; copied files are checked before the official validator runs.

## Prepared tests

`tests_v3/test_output_audit.py` builds two synthetic S1 entities, two targets and
an empty prediction row. Failure cases cover null/unknown IDs, missing/duplicate
pairs, inconsistent accepted sets, non-Boolean acceptance, nonfinite scores and
missing output rows. It uses no challenge rows or labels.

`tests_v3/test_preparation.py` covers archive aliases/collisions and stale or
incomplete test receipts. `tests_v3/test_freeze_v2.py` covers reproduction and
assessment hash/metric prerequisites. None has been executed.

## Snapshot helper limits

The new `freeze_v2.py` requires completed assessment, exact reproduction, integrity
audit, reviewed inventory and target-DF provenance. It reruns reproduction only
when explicitly invoked after execution resumes. Its result is a preserved V2
snapshot, not a portable inference implementation. No snapshot was created.

## Outstanding execution gates

1. Run the synthetic fixtures and access-reviewed recovery suite after resumption.
2. Complete V2 assessment/reproduction; create reviewed snapshot inputs and DF recipe.
3. Implement and verify arbitrary-candidate inference and the actual selected V3
   retrieval materializers/orchestration after development-only measurements.
4. Produce genuine test/source receipts, final inference and both validator results.

No final score, test PASS, new submission ZIP or release-readiness claim is made.
