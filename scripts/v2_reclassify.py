"""Apply the human-approved exposure taxonomy to the preserved 598-source registry.

This does not rebuild the historical registry, open labels, or allocate populations.
Run with Python -B. Only explicit S1 IDs and approved split selectors are decoded
from historical data files. Prior whole-file checksums are retained; exact current
ID membership is checked against every original per-source logical checksum.
The separately authorized profiling supplement reads the GT header and five binary
lines, decoding only the first tab-delimited S1 ID field, never the remainder.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq


CATEGORY_NAMES = {
    "A": "MEMBERSHIP_ONLY", "B": "AGGREGATE_ONLY",
    "C": "DECISION_RELEVANT_ROW_LEVEL", "D": "AMBIGUOUS",
}
CATEGORY_BITS = {"A": 2, "B": 4, "C": 8, "D": 16}
FIT_BIT = 1
EXCLUSION_BITS = CATEGORY_BITS["C"] | CATEGORY_BITS["D"]
LOW_MASK = 255
EXPECTED_SOURCES = 598
RULE_ATTACHMENT = Path("C:/Users/kusha/.codex/attachments/a4288409-6071-42b5-a277-01c337f20d46/Pasted text.txt")
PROFILE_SCRIPT_SHA256 = "547cf781f58b8e56613c05a7cab10ceb8dcb5db40e8a02503154e169879a8bad"
AGGREGATE_OPERATIONS = {
    "matcher_full_train_gt_scan", "matcher_global_target_integrity",
    "development_model_train_target_integrity", "baseline_fullgt_materialization",
    "lightgbm_smoke_fullgt_materialization", "reporting_fullgt_materialization",
    "baseline_gt_file_scan", "integrity_full_train_label_audit",
    "foundation_all_train_normalized_keys",
}
BOUNDED_DOWNSTREAM_OPERATIONS = {
    "baseline_fullgt_materialization", "lightgbm_smoke_fullgt_materialization",
    "reporting_fullgt_materialization", "baseline_gt_file_scan",
}
USED_SELECTORS = {
    "work/split_s1.parquet": "Original val population used for candidate recall, oracle, loss analysis and candidate-policy selection.",
    "work/split_s1_grouped.parquet": "Grouped val population was used in candidate-key-eligibility/oracle diagnostics. This record represents that executed operation, not merely ID membership.",
    "work/model_development_split_manifest.parquet": "Non-fit model_tune and model_threshold cohorts were used downstream in model configuration and threshold selection. The source selector excludes model_fit membership itself.",
    "work/feature_pilot_s1.parquet": "All selected pilot S1s formed the model-development/evaluation cohort, including selected S1s absent from pair artifacts.",
    "work/model_development_samples.parquet": "Selected 50,000 fit S1s, 10,000 baseline-development S1s and all tune S1s were development/evaluation cohorts. Selection-superset provenance retains zero-candidate S1s.",
    "work/model_development_generation_selection.parquet": "Explicit selected candidate/feature-generation cohorts for model development, including zero-candidate S1s.",
    "work/model_tune_us_retry_selection.parquet": "Selected tune retry cohorts used in candidate/feature materialization and model configuration comparison.",
    "work/model_threshold_selection.parquet": "Selected threshold-evaluation cohort used downstream for threshold selection.",
    "work/final_eval_selection.parquet": "Selected historical final-evaluation cohort, including zero-candidate S1s.",
    "work/pilot_candidate_probe_s1.parquet": "Executed candidate-probe selection cohort; explicit selection retains all probed S1s.",
}
ID_COLUMNS = {"entity_id", "source1_entity_id", "s1"}
ALLOWED_FILTERS = {
    "", "split='model_train'", "split='candidate_dev'", "split='model_calibration'",
    "split='val'", "split!='model_fit'",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def logical_sha(ids) -> str:
    h = hashlib.sha256()
    for entity_id in ids:
        h.update((entity_id + "\n").encode("utf-8"))
    return h.hexdigest()


def category_excludes(category: str) -> bool:
    """Unknown or ambiguous exposure fails closed."""
    return category not in {"A", "B", "MEMBERSHIP_ONLY", "AGGREGATE_ONLY"}


def classify_source(row: dict) -> dict:
    """Classify one original registry source at its recorded operation scope."""
    source_id = row.get("source_id", "")
    path = row.get("artifact_path", "")
    operation = source_id.removeprefix("executed_operation:")
    if row.get("status") == "corrupt_file_candidate_dev_superset":
        category = "D"
        reason = "Known historical candidate shard has a corrupt footer; exact stored membership/exposure cannot be recovered. Exclude the proved candidate_dev superset."
        granularity = "unknown stored rows; exact candidate_dev superset available"
        exposed = None
        relevance = "potential decision-relevant candidate rows; ambiguity excluded conservatively"
        confidence = "conservative_high_scope_confidence"
    elif source_id.startswith("executed_matcher_labelled_audit:"):
        category = "B"
        reason = "Human-approved aggregate exception: reviewed code and executed output contain split-wide link counts, singleton counts, means and match-count distributions only. This record is the descriptive-audit operation alone."
        granularity = "population-level descriptive aggregates"
        exposed = False
        relevance = "no row-level result from this operation; separate downstream C sources remain excluded"
        confidence = "high_reviewed_code_and_execution_evidence"
    elif source_id.startswith("executed_operation:") and operation in AGGREGATE_OPERATIONS:
        category = "B"
        reason = "Reviewed operation persisted global integrity/population summaries; processing every S1 during the scan does not itself exclude under the human-approved amendment."
        granularity = "population-level integrity or descriptive aggregate output"
        relevance = "aggregate-only operation; no exclusion under amended rule"
        if operation in BOUNDED_DOWNSTREAM_OPERATIONS:
            reason += " This classification is limited to the original whole-file scan/materialization scope. Subsequent per-S1 pilot-calibration semantic use is category C through pilot/sample/evaluation sources."
            granularity = "whole-file scan or transient materialization; no whole-population row-level output"
            relevance = "broad scan exempt; bounded downstream decisions separately excluded"
        if operation == "foundation_all_train_normalized_keys":
            reason = "Reviewed broad normalized-key operation emits aggregate collision/group diagnostics and ID membership; it exposes no per-S1 labels or candidate outcomes. The separate grouped-val oracle operation is category C."
            granularity = "population-level non-label diagnostics and membership output"
        exposed = False
        confidence = "high_reviewed_operation_scope"
    elif source_id == "artifact:work/matcher_split_manifest.parquet" and row.get("status") == "membership_only_no_touch":
        category = "A"
        reason = "This record is the original full ID/split membership manifest only. Executed uses of its populations are classified separately."
        granularity = "ID and split membership only"
        exposed = False
        relevance = "membership evidence; no decision-relevant per-S1 values"
        confidence = "high_explicit_schema_and_scope"
    elif source_id.startswith("artifact:") and path.startswith("work/"):
        category = "C"
        reason = USED_SELECTORS.get(path, "Historical row-level research/development/evaluation artifact contains explicit S1 labels, candidate rows, features, scores, predictions, errors or decision metrics. Only its S1 ID membership is reread here.")
        granularity = "executed selected S1 cohort; selector includes zero-candidate S1s" if path in USED_SELECTORS else "row-level historical research/development/evaluation output"
        exposed = True
        relevance = "decision-relevant historical engineering or evaluation population"
        confidence = "high_explicit_membership_and_downstream_provenance"
    else:
        category = "D"
        reason = "Unrecognized recorded source scope: aggregate-only or membership-only exposure is not proven. Fail closed using its registered explicit membership or conservative superset."
        granularity = "unresolved historical exposure"
        exposed = None
        relevance = "potential decision-relevant use"
        confidence = "conservative_unresolved"
    excludes = category_excludes(category)
    return {
        "exposure_category": category,
        "exposure_category_name": CATEGORY_NAMES[category],
        "category_reason": reason,
        "persisted_output_granularity": granularity,
        "per_s1_values_exposed": exposed,
        "decision_relevance": relevance,
        "exclusion_action": "exclude_smallest_provable_superset" if category == "D" else "exclude_explicit_membership" if excludes else "disclose_do_not_exclude",
        "excluded_s1_count": row.get("distinct_s1_count", 0) if excludes else 0,
        "confidence_level": confidence,
    }


def write_json(path: Path, payload: dict) -> None:
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)


def run(root: Path, amendment: Path, audit_evidence: Path) -> dict:
    started = time.monotonic()
    root = root.resolve()
    work = root / "work"
    original_path = work / "v2_historical_touch_registry.parquet"
    original_checksums_path = work / "v2_historical_touch_checksums.json"
    original = json.loads(original_checksums_path.read_text(encoding="utf-8"))
    original_hash = sha256(original_path)
    if original_hash != original["artifacts"]["registry"]["sha256"]:
        raise RuntimeError("Preserved historical registry hash mismatch")
    if not amendment.is_file() or not audit_evidence.is_file():
        raise RuntimeError("Human amendment and reviewed aggregate-audit evidence are required")
    rows = pq.read_table(original_path).to_pylist()
    if len(rows) != EXPECTED_SOURCES or len({r["source_id"] for r in rows}) != EXPECTED_SOURCES:
        raise RuntimeError("Expected precisely the preserved 598 unique source records")
    preserved_inputs = [original_path, original_checksums_path,
                        work / "v2_eligible_s1.parquet", work / "v2_historical_touch_report.md"]
    preserved_hashes = {str(p.relative_to(root)): sha256(p) for p in preserved_inputs}
    evidence_checksums = {"human_amendment": {"path": str(amendment.resolve()), "sha256": sha256(amendment)},
                          "aggregate_audit_review": {"path": str(audit_evidence.resolve()), "sha256": sha256(audit_evidence)},
                          "original_registry": {"path": str(original_path.relative_to(root)), "sha256": original_hash}}

    def safe_source(name: str) -> Path:
        path = (root / name).resolve()
        rel = path.relative_to(work)
        if any("test" in part.lower() for part in rel.parts):
            raise RuntimeError("Forbidden test-related source")
        if rel.parts[0].lower() in {"keys", "dataset", "release_stage", "output", "outputs", "submissions"}:
            raise RuntimeError("Forbidden raw/normalized infrastructure or V1 release source")
        if rel.parts[0].lower().startswith("v2_"):
            raise RuntimeError("A V2 output cannot be historical input")
        return path

    con = duckdb.connect()
    con.execute("SET threads=1")
    con.execute("SET memory_limit='512MB'")
    con.execute("SET preserve_insertion_order=false")
    spill = work / "v2_reclassification_tmp"
    spill.mkdir(exist_ok=True)
    con.execute("SET temp_directory=?", [spill.as_posix()])
    matcher = safe_source("work/matcher_split_manifest.parquet")
    development = safe_source("work/model_development_split_manifest.parquet")
    universe: dict[str, int] = {}
    cursor = con.execute("SELECT entity_id FROM read_parquet(?)", [matcher.as_posix()])
    while batch := cursor.fetchmany(65_536):
        for (entity_id,) in batch:
            if entity_id is None or entity_id in universe:
                raise RuntimeError("Invalid or duplicate original S1 universe membership")
            universe[entity_id] = 0
    fit_count = 0
    cursor = con.execute("SELECT entity_id FROM read_parquet(?) WHERE split='model_fit'", [development.as_posix()])
    while batch := cursor.fetchmany(65_536):
        for (entity_id,) in batch:
            if entity_id not in universe or universe[entity_id] & FIT_BIT:
                raise RuntimeError("Invalid/duplicate old model_fit membership")
            universe[entity_id] |= FIT_BIT
            fit_count += 1
    if fit_count != original["old_model_fit_s1"] or len(universe) != original["counts"]["original_train_s1"]:
        raise RuntimeError("Original S1 universe membership counts changed")

    selector_files = set(USED_SELECTORS) | {"work/matcher_split_manifest.parquet"}
    freshly_hashed = {}
    updated = []
    excluded_total = 0
    category_sources = Counter()
    print(f"Amendment reclassification: {len(rows)} preserved sources, ID-only membership verification, no label reads", flush=True)
    for index, row in enumerate(rows):
        classification = classify_source(row)
        category = classification["exposure_category"]
        category_sources[category] += 1
        artifact = safe_source(row["artifact_path"])
        membership = safe_source(row["membership_source_path"])
        if artifact.stat().st_size != row["artifact_bytes"]:
            raise RuntimeError("Historical artifact byte size changed: " + row["artifact_path"])
        if row["artifact_path"] in selector_files:
            if row["artifact_path"] not in freshly_hashed:
                freshly_hashed[row["artifact_path"]] = sha256(artifact)
            if freshly_hashed[row["artifact_path"]] != row["artifact_sha256"]:
                raise RuntimeError("Historical selector file hash changed")
        if row["membership_source_path"] in selector_files:
            if row["membership_source_path"] not in freshly_hashed:
                freshly_hashed[row["membership_source_path"]] = sha256(membership)
            if freshly_hashed[row["membership_source_path"]] != row["membership_source_sha256"]:
                raise RuntimeError("Historical membership source hash changed")
        id_column = row["s1_id_column"]
        selector = row["selection_filter"]
        if id_column not in ID_COLUMNS or selector not in ALLOWED_FILTERS:
            raise RuntimeError("Unapproved historical projection or selector")
        where = " WHERE " + selector if selector else ""
        query = f'SELECT "{id_column}" AS s1_id, count(*) AS id_multiplicity FROM read_parquet(?){where} GROUP BY 1 ORDER BY 1'
        cursor = con.execute(query, [membership.as_posix()])
        ids_hash = hashlib.sha256()
        count = selected_rows = new_excluded = existing_excluded = fit_overlap = new_category = 0
        while batch := cursor.fetchmany(65_536):
            for entity_id, multiplicity in batch:
                if entity_id not in universe:
                    raise RuntimeError("Historical source has an unknown S1 ID")
                count += 1
                selected_rows += multiplicity
                ids_hash.update((entity_id + "\n").encode("utf-8"))
                value = universe[entity_id]
                fit_overlap += bool(value & FIT_BIT)
                new_category += not bool(value & CATEGORY_BITS[category])
                if classification["excluded_s1_count"]:
                    if value & EXCLUSION_BITS:
                        existing_excluded += 1
                    else:
                        new_excluded += 1
                        value = (value & LOW_MASK) | ((index + 1) << 8)
                universe[entity_id] = value | CATEGORY_BITS[category]
        if count != row["distinct_s1_count"] or ids_hash.hexdigest() != row["sorted_s1_id_sha256"]:
            raise RuntimeError("Historical source ID membership checksum changed: " + row["source_id"])
        if selected_rows != row["selected_row_count"] or fit_overlap != row["old_model_fit_overlap_s1"]:
            raise RuntimeError("Historical membership count mismatch: " + row["source_id"])
        excluded_total += new_excluded
        record = dict(row)
        record.update(classification)
        record.update({
            "evidence_path": str(audit_evidence.resolve()),
            "evidence_sha256": evidence_checksums["aggregate_audit_review"]["sha256"],
            "evidence_paths_checksums_json": json.dumps({**evidence_checksums,
                "registered_artifact": {"path": row["artifact_path"], "sha256": row["artifact_sha256"]},
                "registered_membership": {"path": row["membership_source_path"], "sha256": row["membership_source_sha256"]}}, sort_keys=True),
            "original_source_index": row["source_index"],
            "amended_new_excluded_s1": new_excluded,
            "amended_previous_exclusion_overlap_s1": existing_excluded,
            "amended_cumulative_excluded_s1": excluded_total,
            "amended_new_category_s1": new_category,
            "amended_excluded_old_model_fit_s1": fit_overlap if category_excludes(category) else 0,
            "current_id_checksum_verified": True,
            "current_artifact_byte_size_verified": True,
            "whole_file_sha256_verification": "fresh selector-file checksum" if row["artifact_path"] in freshly_hashed else "prior computed checksum retained; current ID checksum and byte size verified; non-ID bytes not rehashed",
        })
        updated.append(record)
        if index < 14 or (index + 1) % 25 == 0 or index == len(rows) - 1:
            print(f"[{index+1}/{len(rows)}] {category} {row['source_id']} ids={count:,} new_excluded={new_excluded:,} elapsed={time.monotonic()-started:.1f}s", flush=True)
    con.close()

    # Supplemental evidence is distinct from the preserved 598 registry records.
    # The user-authorized first-field-only reconstruction never decodes targets.
    profile_evidence_paths = ["scripts/profile_dataset.py", "ULTRA_PLAN.md", "PRD.md"]
    profile_evidence = {name: sha256(root / name) for name in profile_evidence_paths}
    if profile_evidence["scripts/profile_dataset.py"] != PROFILE_SCRIPT_SHA256:
        raise RuntimeError("Reviewed profiling source changed")
    profile_ids = []
    gt_path = root / "dataset/train/train_ground_truth.tsv"
    with gt_path.open("rb") as stream:
        header = stream.readline()
        if header.partition(b"\t")[0].decode("utf-8-sig") != "source1_entity_id":
            raise RuntimeError("Unexpected training GT first-column header")
        for _ in range(5):
            line = stream.readline()
            if not line or b"\t" not in line:
                raise RuntimeError("Could not reconstruct five original profiling S1 IDs")
            entity_id = line.partition(b"\t")[0].decode("utf-8")
            if entity_id not in universe:
                raise RuntimeError("Profiling sample S1 absent from original universe")
            profile_ids.append(entity_id)
    if len(set(profile_ids)) != 5:
        raise RuntimeError("Historical profiling sample has duplicate S1s")
    profile_ids.sort()
    profile_new = profile_overlap = profile_fit = profile_new_fit = 0
    for entity_id in profile_ids:
        value = universe[entity_id]
        profile_fit += bool(value & FIT_BIT)
        if value & EXCLUSION_BITS:
            profile_overlap += 1
        else:
            profile_new += 1
            profile_new_fit += bool(value & FIT_BIT)
            value = (value & LOW_MASK) | ((len(updated) + 1) << 8)
        universe[entity_id] = value | CATEGORY_BITS["D"]
    excluded_total += profile_new
    supplement = {
        "source_id": "supplement:historical_profile_first_five_gt_examples",
        "source_index": len(updated), "original_source_index": None,
        "supplemental_to_original_registry": True,
        "exposure_category": "D", "exposure_category_name": CATEGORY_NAMES["D"],
        "category_reason": "Profiling source prints the first five GT S1/target examples; project phase documentation records profiling complete, but the exact captured row-level output is unavailable. Conservatively exclude precisely those five source-order S1 IDs.",
        "persisted_output_granularity": "historical printed first-five per-S1 GT examples, inferred from reviewed source and completed-phase evidence",
        "per_s1_values_exposed": None,
        "decision_relevance": "potential historically displayed per-S1 labels; conservative supplemental exclusion",
        "exclusion_action": "exclude_smallest_provable_superset",
        "excluded_s1_count": 5, "distinct_s1_count": 5,
        "sorted_s1_id_sha256": logical_sha(profile_ids),
        "s1_ids": profile_ids, "membership_source_path": "dataset/train/train_ground_truth.tsv",
        "membership_reconstruction": "Read header plus exactly five binary lines; decode only line.partition(b'\\t')[0]; never decode, parse or display the target remainder.",
        "columns_decoded_json": json.dumps(["source1_entity_id"]),
        "evidence_path": str(audit_evidence.resolve()),
        "evidence_sha256": evidence_checksums["aggregate_audit_review"]["sha256"],
        "evidence_paths_checksums_json": json.dumps(profile_evidence, sort_keys=True),
        "confidence_level": "conservative_executed_phase_documentation_exact_five_id_scope",
        "old_model_fit_overlap_s1": profile_fit,
        "amended_new_excluded_s1": profile_new,
        "amended_previous_exclusion_overlap_s1": profile_overlap,
        "amended_new_excluded_model_fit_s1": profile_new_fit,
        "amended_cumulative_excluded_s1": excluded_total,
    }
    all_exclusion_sources = updated + [supplement]
    print(f"[supplement] D profiling-first-five ids=5 old_fit={profile_fit} prior_exclusion_overlap={profile_overlap} new_excluded={profile_new} new_excluded_fit={profile_new_fit}", flush=True)

    ordered_ids = sorted(universe)
    category_counts = {}
    for category, bit in CATEGORY_BITS.items():
        ids = [entity_id for entity_id in ordered_ids if universe[entity_id] & bit]
        category_counts[category] = {"name": CATEGORY_NAMES[category], "source_count": category_sources[category],
                                     "supplemental_source_count": 1 if category == "D" else 0,
                                     "total_source_count_including_supplements": category_sources[category] + (1 if category == "D" else 0),
                                     "distinct_s1": len(ids), "sorted_id_sha256": logical_sha(ids),
                                     "old_model_fit_s1": sum(bool(universe[i] & FIT_BIT) for i in ids)}
        del ids
    overlap = {}
    for a, b in itertools.combinations(CATEGORY_BITS, 2):
        ids = [i for i in ordered_ids if universe[i] & CATEGORY_BITS[a] and universe[i] & CATEGORY_BITS[b]]
        overlap[a + "_and_" + b] = {"distinct_s1": len(ids), "sorted_id_sha256": logical_sha(ids),
                                     "old_model_fit_s1": sum(bool(universe[i] & FIT_BIT) for i in ids)}
        del ids
    eligible = [i for i in ordered_ids if universe[i] & FIT_BIT and not universe[i] & EXCLUSION_BITS]
    excluded = [i for i in ordered_ids if universe[i] & EXCLUSION_BITS]
    excluded_fit = [i for i in excluded if universe[i] & FIT_BIT]
    old_fit = [i for i in ordered_ids if universe[i] & FIT_BIT]
    if logical_sha(old_fit) != original["subset_audit"]["old_model_fit_sorted_id_sha256"]:
        raise RuntimeError("Original model_fit logical checksum changed")
    if len(excluded) != excluded_total or len(eligible) + len(excluded_fit) != fit_count:
        raise RuntimeError("Amended exclusion/eligibility accounting failed")

    registry_path = work / "v2_historical_touch_registry_v2.parquet"
    eligible_path = work / "v2_eligible_s1_v2.parquet"
    touched_path = work / "v2_decision_relevant_touched_s1.parquet"
    supplement_path = work / "v2_exposure_supplements.parquet"
    for p in (registry_path, eligible_path, touched_path):
        if p in preserved_inputs:
            raise RuntimeError("Refusing to overwrite original artifact")
    registry_table = pa.Table.from_pylist(updated)
    pq.write_table(registry_table, registry_path, compression="zstd")
    pq.write_table(pa.Table.from_pylist([supplement]), supplement_path, compression="zstd")
    pq.write_table(pa.table({"source1_entity_id": pa.array(eligible, type=pa.string())}), eligible_path, compression="zstd")
    touched_schema = pa.schema([("source1_entity_id", pa.string()), ("first_exclusion_source_id", pa.string()),
                               ("decision_relevant_exposure_categories", pa.string())])
    with pq.ParquetWriter(touched_path, touched_schema, compression="zstd") as writer:
        for offset in range(0, len(excluded), 65_536):
            ids = excluded[offset:offset + 65_536]
            first = [all_exclusion_sources[(universe[i] >> 8) - 1]["source_id"] for i in ids]
            categories = ["C,D" if universe[i] & CATEGORY_BITS["C"] and universe[i] & CATEGORY_BITS["D"]
                          else "C" if universe[i] & CATEGORY_BITS["C"] else "D" for i in ids]
            writer.write_table(pa.Table.from_arrays([pa.array(ids), pa.array(first), pa.array(categories)], schema=touched_schema))
    for name, expected in preserved_hashes.items():
        if sha256(root / name) != expected:
            raise RuntimeError("Original registry/stop artifact changed")
    assumptions = [
        "This is a human-approved eligibility rule revision. The original strict-rule zero-pool stop remains correct and preserved.",
        "A/B exposures are disclosed without per-S1 exclusion. C/D membership alone determines amended exclusion.",
        "Operation records are classified at their original registered scope; broad file scans are not conflated with later bounded per-S1 semantic uses.",
        "An ID-only selector for a known executed decision-relevant population is classified C as that population-use operation; it conservatively includes selected zero-candidate S1s.",
        "Three corrupted historical candidate shards remain D and use the smallest proven candidate_dev superset.",
        "All current per-source exact ID counts, multiplicities, old-fit overlaps and logical checksums match the original registry. Full file checksums for large artifacts are retained from that run; their non-ID bytes were not reread or rehashed. Small selector files were freshly byte-hashed.",
        "The classification applies to recorded executed evidence. Mere unexecuted source-code capability does not establish historical exposure.",
        "A separately documented completed profiling phase printed first-five GT examples. One supplemental D record conservatively excludes precisely five S1 IDs, reconstructed through the explicitly authorized first-tab-field-only binary read. The other GT fields were not decoded or exposed.",
        "Future protected groups must be described as prospectively sealed v2 populations. No claim is made that their GT rows were never scanned or included in aggregates.",
        "This script only reclassifies and computes eligibility; it does not allocate splits, access labels, or start candidate research.",
    ]
    result = {
        "schema_version": 2, "created_utc": datetime.now(timezone.utc).isoformat(),
        "human_approved_rule_revision": True,
        "honest_holdout_terminology": "prospectively sealed v2 populations",
        "holdout_claim": "Their per-S1 labels, outcomes and metrics were not previously exposed or used for decision-relevant candidate/model development according to the reconstructed historical evidence; protected populations are to be prospectively sealed from allocation forward.",
        "original_stop_was_correct_under_original_rule": True,
        "command": "$env:PYTHONDONTWRITEBYTECODE='1'; .venv/Scripts/python.exe -B scripts/v2_reclassify.py",
        "status": "ELIGIBLE_POOL_SUFFICIENT" if len(eligible) >= 400_000 else "BLOCKED_INSUFFICIENT_POOL",
        "original_registry_source_count": len(rows), "reclassified_source_count": len(updated),
        "supplemental_source_count": 1, "supplemental_sources": [supplement],
        "category_counts": category_counts, "category_pair_overlaps": overlap,
        "old_model_fit_s1": fit_count, "decision_relevant_touched_s1": len(excluded),
        "touched_model_fit_s1": len(excluded_fit), "eligible_s1": len(eligible),
        "required_minimum_s1": 400_000, "shortfall_s1": max(0, 400_000-len(eligible)),
        "eligible_id_sha256": logical_sha(eligible),
        "decision_relevant_touched_id_sha256": logical_sha(excluded),
        "excluded_model_fit_id_sha256": logical_sha(excluded_fit),
        "old_model_fit_id_sha256": logical_sha(old_fit),
        "checksum_method": "SHA256 of sorted unique UTF-8 S1 IDs, each followed by LF",
        "overlap_accounting": {"C_union_D": len(excluded), "C_intersection_D": overlap["C_and_D"]["distinct_s1"],
                               "sum_per_source_excluded": sum(r["excluded_s1_count"] for r in all_exclusion_sources),
                               "sum_incremental_new_excluded": sum(r["amended_new_excluded_s1"] for r in all_exclusion_sources),
                               "model_fit_excluded_plus_eligible": len(excluded_fit)+len(eligible),
                               "eligible_intersection_C_or_D": 0},
        "evidence": evidence_checksums, "original_preserved_file_hashes": preserved_hashes,
        "fresh_selector_file_hashes": freshly_hashed,
        "artifacts": {
            "registry": {"path": registry_path.relative_to(root).as_posix(), "sha256": sha256(registry_path)},
            "eligible_s1": {"path": eligible_path.relative_to(root).as_posix(), "sha256": sha256(eligible_path)},
            "touched_s1": {"path": touched_path.relative_to(root).as_posix(), "sha256": sha256(touched_path)},
            "supplements": {"path": supplement_path.relative_to(root).as_posix(), "sha256": sha256(supplement_path)},
        },
        "resources": {"elapsed_seconds": round(time.monotonic()-started, 3), "duckdb_threads": 1,
                      "duckdb_memory_limit": "512MB", "spill_directory": "work/v2_reclassification_tmp"},
        "assumptions": assumptions, "per_source_records": updated,
    }
    report = ["# Human-approved V2 exposure reclassification", "",
              "This applies the approved rule amendment. The previous zero-pool stop was correct under the original rule and remains preserved. Protected groups must be described as **prospectively sealed v2 populations**; this is not a claim of never having been scanned or included in historical aggregates.", "",
              f"All **{len(updated)}** existing registry records were reclassified, with **one supplemental D source** for five profiling-example S1s. Historical artifacts were projected to IDs/split only; the authorized GT supplement decoded only the first S1 field of five binary lines. No label values, target IDs, candidate values, scores, features, or test data were decoded.", "",
              "| Category | Original sources | Supplements | Distinct S1 | Effect |", "|---|---:|---:|---:|---|"]
    for category in CATEGORY_BITS:
        c = category_counts[category]
        report.append(f"| {category}: {c['name']} | {c['source_count']:,} | {c['supplemental_source_count']} | {c['distinct_s1']:,} | {'Exclude' if category_excludes(category) else 'Disclose only'} |")
    report.extend(["", f"Category C/D union: **{len(excluded):,}** S1. C/D overlap: **{overlap['C_and_D']['distinct_s1']:,}**.", "",
                   f"Old model_fit: **{fit_count:,}**. Decision-relevant exclusions within model_fit: **{len(excluded_fit):,}**. Revised eligible pool: **{len(eligible):,}**.", "",
                   f"Gate status: **{result['status']}**. Required minimum: 400,000. Shortfall: {result['shortfall_s1']:,}.", "",
                   f"Supplemental profiling exclusion: five IDs; {profile_fit} old model_fit IDs; {profile_overlap} already in C/D; {profile_new} new exclusions, including {profile_new_fit} newly excluded old model_fit IDs.", "",
                   f"Eligible sorted-ID SHA256: `{result['eligible_id_sha256']}`.", "",
                   "The full-model_train descriptive audit and other broad aggregate/integrity scans are category B at their recorded scopes. Known downstream row-level pilot, candidate, grouped-val, model-development, threshold and final-evaluation cohorts remain category C. Membership-only evidence is category A. Three corrupt candidate files remain category D using candidate_dev supersets.", "",
                   "Exact current ID membership was reprojected and matched every original source count/checksum. Original whole-file checksums are retained for large files; current byte sizes were checked, and small selector files were freshly hashed. Non-ID bytes were not rehashed.", "",
                   "## Assumptions", ""] + ["- " + a for a in assumptions] + ["",
                   "The JSON and Parquet include every original field plus all requested classification, evidence, confidence, exclusion, and amended overlap fields. Original registry, original empty eligible file, and stop report hashes were verified unchanged.", "",
                   f"Elapsed: {result['resources']['elapsed_seconds']:.3f} seconds. One DuckDB thread; 512MB DuckDB memory limit.", ""])
    (work / "v2_historical_touch_reclassification.md").write_text("\n".join(report), encoding="utf-8")
    write_json(work / "v2_historical_touch_reclassification.json", result)
    print(json.dumps({k: result[k] for k in ("status", "category_counts", "old_model_fit_s1", "decision_relevant_touched_s1", "touched_model_fit_s1", "eligible_s1", "eligible_id_sha256", "resources")}, indent=2), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--amendment", type=Path, default=RULE_ATTACHMENT)
    parser.add_argument("--audit-evidence", type=Path, default=Path("work/v2_aggregate_exposure_verification.md"))
    args = parser.parse_args()
    evidence = args.audit_evidence if args.audit_evidence.is_absolute() else args.root / args.audit_evidence
    run(args.root, args.amendment, evidence)


if __name__ == "__main__":
    main()
