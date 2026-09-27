# V3 reset runbook — preparation only

**Current state:** experiments paused by the user. Do not schedule, launch or
resume Python until execution capacity returns and the user resumes execution.
No new final ZIP or validator PASS is claimed.

## What is preserved

- Existing V1 release and all V2 measured artifacts/ledger remain in place.
- Completed full-context V2: model-selection F0.5 **0.9204500338**.
- Initial policy: odd-policy-selection F0.5 **0.9224154794**.
- Retained residual depth 6: odd-policy-selection F0.5 **0.9234484554**.
- Completed capacity 127: model-selection F0.5 **0.9205144354**; its gain over
  full-context is below 0.001. This is not a policy or assessment score.
- Capacity 255 was interrupted at approximately iteration 450. Its log is not
  a completed result; no `result.json` exists. Do not promote an incomplete model.
- No `assessment.json` exists. Do not label development scores generalization.
- Metadata/model recovery snapshot: `work/v3_research_r1/phase0_recovery_snapshot.json`.

Latest source fixes after the last full test run include actual residual decision
score export/reproduction, model-type-aware scoring, and the retired broad
population reader. **They are not yet tested.** Earlier PASS receipts cannot be
used as evidence for these later changes.

## First execution sequence after reset

Run from `C:\Projects\amazon-challenge`. Each command is a separate gate; stop
and diagnose a failure. Preserve every new result under a new filename.

1. Inspect git status and check SHA bindings against the recovery snapshot.
2. Run synthetic tests for `test_residual.py`, `test_matcher_sprint.py`,
   `test_entity_utility.py`, `test_v3_firewall.py`, then `tests_v3`.
3. Run the access-reviewed compatibility suite with a NEW output path:

   ```powershell
   .venv\Scripts\python.exe -B scripts\v2_verify.py --result-path work/v3_research_r1/recovery_tests.json
   ```

   The four old tests remain explicitly skipped because they open historical
   row-level labels; this is justified by the continuing sealed-label boundary.
4. Inspect the frozen policy selected by `active_policy_path()`; currently it is
   `decision_policy_refined.json`, bound to full-context and residual-depth6 model
   hashes. Do not resume extra model searches as a substitute for V3 retrieval.
5. Verify no assessment exists and execute the planned one-time V2 assessment,
   then exact reproduction:

   ```powershell
   .venv\Scripts\python.exe -u -B scripts\v2_matcher_sprint.py assessment
   .venv\Scripts\python.exe -u -B scripts\v2_matcher_sprint.py reproduce
   .venv\Scripts\python.exe -B scripts\v2_matcher_audit.py
   ```

   Before these large runs, print projections from known feature/matrix counts,
   artifact sizes and development runtimes. Compare raw scores, actual corrected
   decision scores and accepted flags. Do not tune from assessment results.
6. Freeze the selected inference artifacts and source SHA manifest into a new
   `artifacts/final_candidate_matcher_v2/` directory, including teacher V1 model,
   base V2 model, residual model if active, both feature specs, preprocessing,
   target-DF recipe, calibration and decision files, package versions and exact
   reproduction command. The present research scorer still has research-only
   paths: implement and parity-test the arbitrary-candidate adapter before claiming
   this is portable or ready for test.
7. Run V3 gap analysis on development roles only:

   ```powershell
   .venv\Scripts\python.exe -u -B scripts\v3\gap_analysis.py --execute
   ```

   The command is prepared but unexecuted. Keep role3 errors out of V3 design.
8. Use `v3_experiment_queue.json` to choose measured high-value passes. Adapt
   existing complete-target DF receipts to the V3 schema after checking source
   hashes, normalization, text-length limits and target coverage. Do not mark an
   index complete merely because an old file exists. Run bounded ngram preflight,
   then materialize one selected pass with partition receipts and audit it before
   any GT join. Sister expansion must use observable frozen matcher seeds.
9. Evaluate all serious candidate unions through the same frozen matcher, including
   full truth denominators and empty S1s. Keep a Pareto frontier and reject expensive
   <0.001 final-F0.5 changes. Only then consider matcher re-optimization.

## Prepared release tools and contracts

`scripts/v3/audit_outputs.py` consumes a `TEST_INFERENCE_COMPLETE` manifest:

- `candidate_parts`: exact final matcher input Parquets; each has `path`, `sha256`.
- `score_parts`: complete score Parquets with S1, target and final `accepted`; each
  has `path`, `sha256`, `candidate_input_sha256`. `accepted` must be nonnull BOOLEAN;
  `decision_score` must be nonnull, finite FLOAT/DOUBLE. Pair IDs must be nonnull.
- `matching`, `candidate`: final TSV `path` and `sha256`.
- `model_sha`, `retrieval_policy_sha`, `feature_manifest_sha`, `threshold_policy_sha`,
  `decision_policy_sha`: actual frozen dependencies used by inference, also bound
  into the release configuration.
- `preflight`: projected rows/bytes, partition count, expected runtime seconds,
  peak RAM bytes and temporary disk bytes; every value must be measured/projected,
  not null. The audit prints these before comparing all 16 partitions.

It checks full S1 universe, duplicate pairs, valid target IDs, exact candidate
identity against both matcher inputs and scores, and accepted-set identity.
It writes a new audit receipt and never modifies inference artifacts.

`scripts/v3/package_release.py` consumes a reviewed release config:

- status `ARCHITECTURE_FROZEN_TEST_INFERENCE_AUDITED`;
- output-audit, inference-manifest and project-test-receipt `path` and `sha256`;
- `code_commit`, exact reproduction command, model/retrieval/feature/threshold/
  decision hashes;
- explicit `files` entries: source path, archive path, SHA256.
- `inference_artifacts`: map each of the five inference SHA field names to its
  exact archive path; the packaged bytes and audited inference receipt must agree.

The project test receipt must have `status: PASS`, the same `code_commit`, positive
`tests_run`, `commands` containing argv and zero `exit_code`, and `source_files`
entries with `path` and `sha256` for every packaged Python/config/model-text file.
Create this receipt from real test runs with inputs hashed before and after them;
do not turn the old test receipt into a new PASS by copying it. Packaged Python
and requirements bytes must also exist at the stated Git commit. Receipt generation
and the final pipeline integration tests still need implementation/validation.

The official challenge requires both TSVs, runnable source, pinned requirements,
README and `Documentation_template.md`. Use **Kushagra Garg and Tanisha Mandavia**
and team **broCode** in the updated methodology. Include required model artifacts
for reproducibility. Do not use the old V1 packager without adapting its frozen
V1 assumptions.

Example commands, only after manifests contain real measured values:

```powershell
.venv\Scripts\python.exe -B scripts\v3\audit_outputs.py --execute --manifest work/v3_test/inference_manifest.json --output work/v3_release/output_audit.json
.venv\Scripts\python.exe -B scripts\v3\package_release.py --execute --config work/v3_release/release_config.json --destination work/v3_release/verified_package
```

The packager runs the official validator on the staged originals, creates a new
`broCode_submission.zip`, verifies CRC and allowlist, extracts to a fresh directory,
checks every extracted hash, runs the validator again and writes the final release
manifest. Existing root ZIP/outputs are never overwritten by this tool. Promote a
new release to the requested root paths only after preserving the prior release
and passing every gate.

## Remaining implementation and validation

- Test every new preparation script with tiny synthetic fixtures. No new script
  is tested merely because its source has been reviewed.
- Implement arbitrary-candidate V2 context/feature inference with complete S1
  neighborhoods and open-set countries, including France.
- Implement only the retrieval materializers selected by measured gap/preflight
  results; current queue entries are proposals, not executed passes.
- Build the actual frozen pipeline's resumable test orchestration and allowlist
  after its selected passes are known. A generic archive tool is not that pipeline.
- Run a separate integration test that introduces wrong/duplicate/missing pairs
  and proves output audit failure before using it for the release.
- No sealed population is silently authorized by this runbook. V3 final role3
  comparison must disclose prior V2 baseline exposure; it is not an untouched
  challenge holdout or a verified 0.99 result.

## Prepared V2 snapshot helper

`scripts/v3/freeze_v2.py` is source preparation only. After recovery tests,
assessment, reproduction and the integrity audit, supply a reviewed JSON config:

- `status: REVIEWED_V2_SNAPSHOT_INPUTS`;
- `files`: explicit `role`, `source`, `snapshot_path`, `sha256` entries, one per role;
- required roles: `base_model`, `teacher_model`, `teacher_policy`, `model_manifest`,
  `feature_spec`, `decision_policy`, `calibration`, `preprocessing`, `target_df_recipe`,
  `requirements`, plus `residual_model` for the selected residual decision policy;
- `target_df_recipe`: status `VERIFIED_COMPLETE_TARGET_RECIPE`, `labels_used: false`,
  `indexes` with path/SHA bindings, normalization SHA matching preprocessing, exact
  `rebuild_command`, complete target coverage and documented country/text rules;
- `reproduction_preflight`: the same six resource fields used for output auditing.

The helper checks assessment/audit/dependency hashes, reruns exact reproduction,
checks that source and artifact bytes stayed fixed, and copies only the explicit
inventory into a new directory under `artifacts`. It does not overwrite an earlier
snapshot. It currently supports the selected LightGBM/refined policy only.

```powershell
.venv\Scripts\python.exe -B scripts\v3\freeze_v2.py --execute --config work/v3_research_r1/v2_snapshot_inputs.json
```

That input config and verified DF recipe do **not** exist yet: populate them from
the real recovery evidence. The output is `REPRODUCED_V2_SNAPSHOT`, with
`portable_adapter_ready: false` and `ready_for_test_inference: false`. Copying a
model does not satisfy the arbitrary-candidate adapter/parity gate.
