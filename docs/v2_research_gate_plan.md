# V2 candidate research: eligibility gate

**Goal:** Preserve the submitted V1 and determine whether the requested untouched research populations can be allocated.

**Architecture:** Separate V2 scripts and `er/candidates_v2/`; historical membership is reconstructed using ID-only projections. Executed historical code and checksummed reports establish the scope of previous label use. No raw labels or test data are needed for this gate.

**Tech stack:** Existing Python environment, PyArrow, DuckDB, SHA-256 and Git.

**Specification:** User brief dated 2026-09-27, attachment `8d40122b-03b1-49ac-8107-46210491c0f6/Pasted text.txt`, especially sections 0, 1 and 2A.

## Constraints and review focus

- Keep all V1 source, release artifacts, outputs and documentation unchanged.
- Do not infer historical exposure from old `model_fit` membership.
- Bind historical operations to execution evidence and exact ID manifests.
- Do not read raw training GT, real test data, or historical row-level label values.
- If fewer than 400,000 eligible S1s remain, stop before V2 allocation or label access. Do not implement retrieval passes or train models.
- Distinguish zero allocated populations from a valid split containing zero entities.
- Report incomplete test coverage when existing tests require forbidden label reads.

## Tasks

1. `scripts/v2_preserve.py`: verify all frozen artifacts, preserve `release_v1`, create read-only backup and source snapshot, emit immutability proof.
2. `scripts/v2_build_registry.py`: inventory and checksum historical sources, reconstruct touched IDs, calculate the exact difference from old `model_fit`, emit registry and evidence report.
3. `er/candidates_v2/firewall.py` and `scripts/v2_close_gate.py`: assess fixed allocation requirements; write a blocked split marker and access ledger if insufficient. Never create a reduced allocation automatically.
4. `tests_v2/test_firewall.py`: use synthetic fixtures for historical exclusion and fail-closed permission checks. Verify actual gate artifacts without labels.
5. `scripts/v2_verify.py`: discover all V1 tests, explicitly skip assertions requiring real row-level labels, run permitted assertions with V2 temporary paths, run V2 tests, and record exact results.
6. Recheck V1 hashes and Git diff; write final report with every requested research output marked either delivered or blocked by section 2A.

## Current evidence and stop rule

The executed original matcher split audit calculated descriptive label aggregates for all 1,765,608 `model_train` S1s. Exact manifest membership places all 1,655,792 later `model_fit` IDs inside that audited population. This evidence makes the eligible difference empty under the stated rule. The remaining work is evidence packaging and verification, not retrieval research.
