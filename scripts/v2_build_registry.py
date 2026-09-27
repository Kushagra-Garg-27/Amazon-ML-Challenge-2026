"""Close the V2 historical-touch gate with S1-ID-only historical projections.

Run from the repository root with ``.venv/Scripts/python scripts/v2_build_registry.py``.
All V1 inputs are read-only. Full-byte SHA256 reads are integrity reads, not field
decoding. Only explicit S1 IDs and approved split selectors enter SQL projections.
No raw dataset path, test artifact, label, target, feature, or score is decoded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq


EVIDENCE_HASHES = {
    "code/business_entity_resolution/src/er/matcher/splits.py":
        "7f7e4de29292f2b3227647c265267a5bf9fca37c4ff88fb004f5ee27b37fa258",
    "work/matcher_split_checksums.json":
        "8d7cc8214a9028bbbc3c4b3bfe31af56a8337f6f01c181b544d970435cb0c0d9",
    "work/matcher_split_run.log":
        "8d7cc8214a9028bbbc3c4b3bfe31af56a8337f6f01c181b544d970435cb0c0d9",
}
EXPECTED_ROLE_COUNTS = {
    "model_train": 1_765_608, "model_calibration": 110_341,
    "model_final_eval": 110_341, "candidate_dev": 220_531,
}
ROLE_BITS = {"model_train": 1, "model_calibration": 2,
             "model_final_eval": 4, "candidate_dev": 8}
FIT_BIT = 16
ROLE_MASK = 255
ID_COLUMNS = {"entity_id", "source1_entity_id", "s1"}
REGISTRY_SCHEMA = pa.schema([
    ("source_index", pa.int32()), ("source_id", pa.string()),
    ("artifact_path", pa.string()), ("artifact_bytes", pa.int64()),
    ("artifact_sha256", pa.string()), ("s1_id_column", pa.string()),
    ("selection_filter", pa.string()), ("columns_decoded_json", pa.string()),
    ("physical_footer_rows", pa.int64()), ("selected_row_count", pa.int64()),
    ("distinct_s1_count", pa.int64()), ("sorted_s1_id_sha256", pa.string()),
    ("previously_touched_overlap_s1", pa.int64()), ("new_touched_s1", pa.int64()),
    ("old_model_fit_overlap_s1", pa.int64()), ("cumulative_touched_s1", pa.int64()),
    ("outside_train_s1_count", pa.int64()), ("exact_stored_membership", pa.bool_()),
    ("contributes_touch", pa.bool_()), ("status", pa.string()),
    ("membership_source_path", pa.string()), ("membership_source_sha256", pa.string()),
    ("provenance", pa.string()), ("operation_evidence_json", pa.string()),
    ("elapsed_seconds", pa.float64()),
])


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def logical_sha(ids) -> str:
    h = hashlib.sha256()
    for entity_id in ids:
        h.update(entity_id.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def write_json(path: Path, payload) -> None:
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)


def run(root: Path) -> dict:
    start = time.monotonic()
    root = root.resolve()
    work = root / "work"
    inventory_path = work / "v2_historical_inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    artifacts = inventory["artifacts"]
    if len(artifacts) != 583:
        raise RuntimeError("Historical inventory changed; re-review its source scope")
    hashes = {}
    hashed_bytes = 0

    def integrity(path: Path) -> str:
        nonlocal hashed_bytes
        key = str(path.resolve())
        if key not in hashes:
            hashes[key] = sha256(path)
            hashed_bytes += path.stat().st_size
        return hashes[key]

    evidence = {}
    for name, expected in EVIDENCE_HASHES.items():
        path = root / name
        actual = integrity(path)
        if actual != expected:
            raise RuntimeError(f"Historical execution evidence changed: {name}")
        evidence[name] = {"sha256": actual, "matches_reviewed_evidence": True}
    assert evidence["work/matcher_split_checksums.json"]["sha256"] == \
        evidence["work/matcher_split_run.log"]["sha256"]

    def historical_path(name: str) -> Path:
        path = (root / name).resolve()
        relative = path.relative_to(work)
        if any("test" in part.lower() for part in relative.parts):
            raise RuntimeError(f"Forbidden test-related path: {name}")
        if relative.parts[0].lower() in {"keys", "dataset", "release_stage", "output", "outputs", "submissions"}:
            raise RuntimeError(f"Forbidden infrastructure/output path: {name}")
        if relative.parts[0].lower().startswith("v2_"):
            raise RuntimeError(f"V2 output cannot be historical evidence: {name}")
        return path

    spill = work / "v2_registry_tmp"
    spill.mkdir(exist_ok=True)
    con = duckdb.connect()
    con.execute("SET threads=1")
    con.execute("SET memory_limit='512MB'")
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET temp_directory=?", [spill.as_posix()])
    matcher = historical_path("work/matcher_split_manifest.parquet")
    development = historical_path("work/model_development_split_manifest.parquet")

    # One integer per S1 packs original membership, the old-fit flag, and the
    # first contributing source index. No label or feature is loaded into memory.
    universe: dict[str, int] = {}
    counts = {role: 0 for role in ROLE_BITS}
    cursor = con.execute("SELECT entity_id, split FROM read_parquet(?)", [matcher.as_posix()])
    while batch := cursor.fetchmany(65_536):
        for entity_id, role in batch:
            if entity_id is None or entity_id in universe or role not in ROLE_BITS:
                raise RuntimeError("Invalid or duplicate original matcher membership")
            universe[entity_id] = ROLE_BITS[role]
            counts[role] += 1
    if counts != EXPECTED_ROLE_COUNTS:
        raise RuntimeError(f"Original matcher role counts changed: {counts}")
    model_fit_count = 0
    fit_hash = hashlib.sha256()
    fit_outside_model_train = 0
    previous_fit_id = None
    cursor = con.execute("SELECT entity_id FROM read_parquet(?) WHERE split='model_fit' ORDER BY entity_id",
                         [development.as_posix()])
    while batch := cursor.fetchmany(65_536):
        for (entity_id,) in batch:
            if entity_id == previous_fit_id:
                raise RuntimeError("Duplicate old model_fit membership")
            previous_fit_id = entity_id
            current = universe.get(entity_id, 0)
            if not current & ROLE_BITS["model_train"]:
                fit_outside_model_train += 1
            if not current:
                raise RuntimeError("Old model_fit contains an unknown S1")
            universe[entity_id] = current | FIT_BIT
            fit_hash.update((entity_id + "\n").encode("utf-8"))
            model_fit_count += 1
    if model_fit_count != 1_655_792 or fit_outside_model_train:
        raise RuntimeError("Old model_fit subset proof did not match reviewed membership")

    sources = []
    for role in ("model_train", "candidate_dev", "model_calibration"):
        sources.append({
            "source_id": "executed_matcher_labelled_audit:" + role,
            "artifact_path": "work/matcher_split_manifest.parquet",
            "id_column": "entity_id", "selector": f"split='{role}'",
            "contributes": True, "exact": True,
            "status": "executed_population_audit",
            "provenance": "Executed historical labelled descriptive audit, not membership-only contamination. "
                          "matcher/splits.py lines 52-74 audit each non-final-eval role. "
                          "Reviewed code and byte-identical persisted checksum JSON/run log are pinned by SHA256. "
                          "Only ID/split membership is reconstructed here; no labelled diagnostic value is decoded.",
        })
    first = next(a for a in artifacts if a["path"] == "work/final_eval_selection.parquet")
    ordered_artifacts = [first] + [a for a in artifacts if a is not first]
    for artifact in ordered_artifacts:
        name = artifact["path"]
        selector = ""
        contributes = True
        status = "historical_artifact"
        provenance = "Exact explicit S1 membership projected from this historical row-level artifact."
        if name in {"work/split_s1.parquet", "work/split_s1_grouped.parquet"}:
            selector = "split='val'"
            status = "historically_audited_selected_population"
            provenance = "Only historical candidate-dev/grouped diagnostic val membership contributes touch."
        elif name == "work/model_development_split_manifest.parquet":
            selector = "split!='model_fit'"
            status = "historically_used_non_fit_population"
            provenance = "Only old tune/threshold roles contribute; model_fit role membership is a source pool only."
        elif name == "work/matcher_split_manifest.parquet":
            contributes = False
            status = "membership_only_no_touch"
            provenance = "Full manifest is membership evidence only. Executed audit sources separately justify historical touches."
        sources.append({"source_id": "artifact:" + name, "artifact_path": name,
                        "id_column": artifact["s1_id_column"], "selector": selector,
                        "contributes": contributes, "exact": True, "status": status,
                        "provenance": provenance})
    # These independently reconstructed operation scopes preserve broad access
    # separately from bounded semantic use. They add no IDs after the first four
    # sources cover every top-level population. Never rerun their label queries.
    operation_specs = [
        ("matcher_full_train_gt_scan", "work/matcher_split_manifest.parquet", "",
         "Whole-training GT file scan to construct temporary nonempty-link gt; scanned population is all train S1. Retained link-bearing subset is not reopened.",
         ["code/business_entity_resolution/src/er/matcher/splits.py", "work/matcher_split_run.log"]),
        ("matcher_global_target_integrity", "work/matcher_split_manifest.parquet", "",
         "Global cross-split labelled-target isolation check; population scope is full train S1. Distinct from descriptive split diagnostics.",
         ["code/business_entity_resolution/src/er/matcher/splits.py", "work/matcher_split_checksums.json"]),
        ("development_model_train_target_integrity", "work/model_development_split_manifest.parquet", "",
         "Target-isolation integrity audit scoped to every inner development manifest S1, equivalently all top-level model_train. This operation scope includes fit; membership alone does not contribute.",
         ["code/business_entity_resolution/src/er/matcher/development.py", "work/model_development_split_checksums.json"]),
        ("baseline_fullgt_materialization", "work/matcher_split_manifest.parquet", "",
         "Whole-training nonempty-link GT materialization into temporary fullgt; later semantic diagnostics narrowed to pilot calibration. Registry IDs describe scan population, not retained labelled rows.",
         ["code/business_entity_resolution/src/er/matcher/baseline.py", "work/baseline_model_run.log"]),
        ("lightgbm_smoke_fullgt_materialization", "work/matcher_split_manifest.parquet", "",
         "Whole-training nonempty-link GT materialization into temporary fullgt before pilot-calibration diagnostics; IDs describe scan population.",
         ["code/business_entity_resolution/src/er/matcher/lightgbm_smoke.py", "work/lightgbm_smoke.json"]),
        ("reporting_fullgt_materialization", "work/matcher_split_manifest.parquet", "",
         "Whole-training nonempty-link GT materialization into temporary fullgt before bounded report diagnostics; IDs describe scan population.",
         ["code/business_entity_resolution/src/er/matcher/reporting.py", "work/baseline_model_report.md"]),
        ("baseline_gt_file_scan", "work/matcher_split_manifest.parquet", "",
         "baseline.py truth_for iterates the whole training GT text file while retaining only wanted pilot IDs. This is file-scan scope; retained semantic scope remains bounded.",
         ["code/business_entity_resolution/src/er/matcher/baseline.py", "work/baseline_model_run.log"]),
        ("integrity_full_train_label_audit", "work/matcher_split_manifest.parquet", "",
         "Historical whole-training label summaries, referential integrity, and target-sharing checks. Existing source/report attests execution; neither raw GT nor persisted metric values are decoded again.",
         ["scripts/integrity_check.py", "work/integrity_report.md"]),
        ("foundation_all_train_normalized_keys", "work/matcher_split_manifest.parquet", "",
         "Historical unlabelled normalized-key scan of every training S1 for split collision/group diagnostics. Labelled candidate-coverage scope was old validation only.",
         ["scripts/foundation_audit.py", "work/foundation_audit.md"]),
    ]
    broad_operations = []
    for operation, membership, selector, provenance, evidence_paths in operation_specs:
        operation_evidence = {}
        for evidence_name in evidence_paths:
            evidence_path = (root / evidence_name).resolve()
            if not evidence_path.is_relative_to(root) or any("test" in part.lower() for part in Path(evidence_name).parts):
                raise RuntimeError("Forbidden operation evidence path")
            operation_evidence[evidence_name] = integrity(evidence_path)
        broad_operations.append({"source_id": "executed_operation:" + operation,
                                 "artifact_path": membership, "id_column": "entity_id",
                                 "selector": selector, "contributes": True, "exact": True,
                                 "status": "reconstructed_executed_operation_scope",
                                 "provenance": provenance + " See work/v2_historical_code_evidence.md. Operation membership is the exact reviewed population scope; it is not a claim about per-pair stored membership.",
                                 "operation_evidence": operation_evidence})
    sources[4:4] = broad_operations
    for artifact in inventory["unreadable_parquet"]:
        sources.append({"source_id": "conservative:" + artifact["path"],
                        "artifact_path": artifact["path"], "id_column": "entity_id",
                        "selector": "split='candidate_dev'", "contributes": True,
                        "exact": False, "status": "corrupt_file_candidate_dev_superset",
                        "membership_source": "work/matcher_split_manifest.parquet",
                        "provenance": "Corrupt quarantined candidate shard: stored exact membership unavailable. "
                                      "Use the smallest proved selected population, candidate_dev, as a conservative superset. "
                                      "The reported ID count/hash describe that superset, not recovered corrupt rows."})

    registry = []
    touched_count = 0
    logical_decoded_rows = 0
    print(f"Registry start: {len(sources)} sources; {len(universe):,} S1; old model_fit {model_fit_count:,}; "
          f"model_fit outside model_train {fit_outside_model_train}; threads=1 memory=512MB", flush=True)
    for index, source in enumerate(sources):
        source_start = time.monotonic()
        artifact = historical_path(source["artifact_path"])
        membership = historical_path(source.get("membership_source", source["artifact_path"]))
        id_column = source["id_column"]
        if id_column not in ID_COLUMNS:
            raise RuntimeError(f"Unapproved ID column: {id_column}")
        physical_rows = None
        if source["exact"]:
            footer = pq.ParquetFile(artifact)
            physical_rows = footer.metadata.num_rows
            if id_column not in footer.schema_arrow.names:
                raise RuntimeError(f"Missing explicit S1 ID: {artifact}")
        artifact_hash = integrity(artifact)
        membership_hash = integrity(membership)
        selector = source["selector"]
        where = " WHERE " + selector if selector else ""
        # count(*) is only ID multiplicity. No label-dependent aggregate is computed.
        query = f'SELECT "{id_column}" AS s1_id, count(*) AS id_multiplicity FROM read_parquet(?){where} GROUP BY 1 ORDER BY 1'
        cursor = con.execute(query, [membership.as_posix()])
        ids_hash = hashlib.sha256()
        distinct = selected_rows = overlap = newly_touched = fit_overlap = outside = 0
        while batch := cursor.fetchmany(65_536):
            for entity_id, multiplicity in batch:
                if entity_id is None or not isinstance(entity_id, str) or not entity_id:
                    raise RuntimeError(f"Invalid S1 identifier in {source['artifact_path']}")
                distinct += 1
                selected_rows += multiplicity
                ids_hash.update((entity_id + "\n").encode("utf-8"))
                current = universe.get(entity_id, 0)
                if not current:
                    outside += 1
                    continue
                if current & FIT_BIT:
                    fit_overlap += 1
                if current >> 8:
                    overlap += 1
                elif source["contributes"]:
                    newly_touched += 1
                    universe[entity_id] = (current & ROLE_MASK) | ((index + 1) << 8)
        if outside:
            raise RuntimeError(f"{source['artifact_path']} has {outside} IDs outside the original train-S1 universe")
        touched_count += newly_touched
        logical_decoded_rows += selected_rows
        row = {
            "source_index": index, "source_id": source["source_id"],
            "artifact_path": source["artifact_path"], "artifact_bytes": artifact.stat().st_size,
            "artifact_sha256": artifact_hash, "s1_id_column": id_column,
            "selection_filter": selector,
            "columns_decoded_json": json.dumps([id_column] + (["split"] if selector else [])),
            "physical_footer_rows": physical_rows, "selected_row_count": selected_rows,
            "distinct_s1_count": distinct, "sorted_s1_id_sha256": ids_hash.hexdigest(),
            "previously_touched_overlap_s1": overlap, "new_touched_s1": newly_touched,
            "old_model_fit_overlap_s1": fit_overlap, "cumulative_touched_s1": touched_count,
            "outside_train_s1_count": outside, "exact_stored_membership": source["exact"],
            "contributes_touch": source["contributes"], "status": source["status"],
            "membership_source_path": source.get("membership_source", source["artifact_path"]),
            "membership_source_sha256": membership_hash, "provenance": source["provenance"],
            "operation_evidence_json": json.dumps(source.get("operation_evidence", evidence if source["status"] == "executed_population_audit" else {}), sort_keys=True),
            "elapsed_seconds": round(time.monotonic() - source_start, 3),
        }
        registry.append(row)
        print(f"[{index+1}/{len(sources)}] {source['artifact_path']} ids={distinct:,} "
              f"new={newly_touched:,} overlap={overlap:,} elapsed={time.monotonic()-start:.1f}s", flush=True)
    con.close()

    touched_path = work / "v2_historical_touched_s1.parquet"
    eligible_path = work / "v2_eligible_s1.parquet"
    registry_path = work / "v2_historical_touch_registry.parquet"
    touched_schema = pa.schema([("source1_entity_id", pa.string()), ("first_source_id", pa.string())])
    eligible_schema = pa.schema([("source1_entity_id", pa.string())])
    touched_partial = touched_path.with_suffix(".partial.parquet")
    touched_hash = hashlib.sha256()
    eligible_ids = []
    fit_touched = 0
    touched_written = 0
    ids_batch, first_batch = [], []
    with pq.ParquetWriter(touched_partial, touched_schema, compression="zstd") as writer:
        for entity_id in sorted(universe):
            value = universe[entity_id]
            first_source = (value >> 8) - 1
            if first_source >= 0:
                touched_written += 1
                fit_touched += bool(value & FIT_BIT)
                touched_hash.update((entity_id + "\n").encode("utf-8"))
                ids_batch.append(entity_id)
                first_batch.append(registry[first_source]["source_id"])
                if len(ids_batch) == 65_536:
                    writer.write_table(pa.Table.from_arrays([pa.array(ids_batch), pa.array(first_batch)], schema=touched_schema))
                    ids_batch, first_batch = [], []
            elif value & FIT_BIT:
                eligible_ids.append(entity_id)
        if ids_batch:
            writer.write_table(pa.Table.from_arrays([pa.array(ids_batch), pa.array(first_batch)], schema=touched_schema))
    os.replace(touched_partial, touched_path)
    if touched_written != touched_count or sum(row["new_touched_s1"] for row in registry) != touched_count:
        raise RuntimeError("Touch accounting mismatch")
    eligible_partial = eligible_path.with_suffix(".partial.parquet")
    pq.write_table(pa.Table.from_arrays([pa.array(eligible_ids, type=pa.string())], schema=eligible_schema),
                   eligible_partial, compression="zstd")
    os.replace(eligible_partial, eligible_path)
    registry_partial = registry_path.with_suffix(".partial.parquet")
    pq.write_table(pa.Table.from_pylist(registry, schema=REGISTRY_SCHEMA), registry_partial, compression="zstd")
    os.replace(registry_partial, registry_path)

    model_train_hash = logical_sha(entity_id for entity_id in sorted(universe)
                                   if universe[entity_id] & ROLE_BITS["model_train"])
    result = {
        "schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
        "command": "$env:PYTHONDONTWRITEBYTECODE='1'; .venv/Scripts/python.exe -B scripts/v2_build_registry.py",
        "eligible_s1": len(eligible_ids), "old_model_fit_s1": model_fit_count,
        "touched_model_fit_s1": fit_touched, "historically_touched_s1": touched_count,
        "eligible_id_sha256": logical_sha(eligible_ids),
        "purpose": "Historical-touch gate closure only; no V2 allocation or research was performed.",
        "status": "blocked_insufficient_untouched_s1" if len(eligible_ids) < 400_000 else "eligible_population_available",
        "resources": {"duckdb_threads": 1, "duckdb_memory_limit": "512MB",
                      "spill_directory": "work/v2_registry_tmp", "elapsed_seconds": round(time.monotonic()-start, 3),
                      "unique_input_bytes_sha256_read": hashed_bytes,
                      "selected_id_rows_processed_including_repeated_sources": logical_decoded_rows},
        "access_contract": {"decoded_fields": "Explicit S1 ID plus split only where a selector requires it; no other values decoded.",
                            "integrity_reads": "Full-byte SHA256 reads do not decode labels, targets, features, scores, or other fields.",
                            "forbidden_data_not_read": ["raw train ground truth", "test data and test artifacts", "V1 release outputs"],
                            "v1_inputs_modified": False},
        "execution_evidence": evidence,
        "inventory": {"path": "work/v2_historical_inventory.json", "sha256": sha256(inventory_path),
                      "explicit_s1_artifacts": len(artifacts), "corrupt_conservative_sources": len(inventory["unreadable_parquet"])},
        "operation_inventory": {"broad_read_operations": len(broad_operations),
                                "code_evidence_path": "work/v2_historical_code_evidence.md",
                                "code_evidence_sha256": sha256(work / "v2_historical_code_evidence.md")},
        "subset_audit": {"old_model_fit_s1": model_fit_count, "old_model_train_s1": counts["model_train"],
                         "old_model_fit_minus_model_train": fit_outside_model_train,
                         "old_model_fit_subset_of_model_train": fit_outside_model_train == 0,
                         "old_model_fit_sorted_id_sha256": fit_hash.hexdigest(),
                         "old_model_train_sorted_id_sha256": model_train_hash,
                         "all_s1_non_model_fit": len(universe)-model_fit_count,
                         "model_train_non_model_fit": counts["model_train"]-model_fit_count,
                         "evidence": "Executed labelled population audit covers all model_train S1; model_fit membership alone is not touch evidence."},
        "counts": {"original_train_s1": len(universe), "registry_sources": len(registry),
                   "touched_s1": touched_count, "old_model_fit_touched_s1": fit_touched,
                   "eligible_old_model_fit_minus_touched_s1": len(eligible_ids),
                   "required_minimum_untouched_s1": 400_000,
                   "minimum_shortfall_s1": max(0, 400_000-len(eligible_ids))},
        "logical_checksums": {"method": "SHA256 of lexicographically sorted unique UTF-8 S1 IDs, each followed by LF",
                              "touched_s1": touched_hash.hexdigest(), "eligible_s1": logical_sha(eligible_ids)},
        "outputs": {"registry": {"path": registry_path.relative_to(root).as_posix(), "sha256": sha256(registry_path)},
                    "touched_s1": {"path": touched_path.relative_to(root).as_posix(), "sha256": sha256(touched_path)},
                    "eligible_s1": {"path": eligible_path.relative_to(root).as_posix(), "sha256": sha256(eligible_path)}},
        "per_source_records": registry,
        "limitations": ["Three corrupt quarantine sources use the exact candidate_dev superset membership; their stored row membership remains unknown.",
                        "Pinned code and byte-identical persisted audit log/checksum file provide the reviewed historical execution evidence; label-derived diagnostic values are not reread.",
                        "Per-source overlap is with earlier contributing sources; contribution counts depend on the documented deterministic ordering."]
    }
    result["artifacts"] = result["outputs"]
    checksums_path = work / "v2_historical_touch_checksums.json"
    write_json(checksums_path, result)
    report = ["# V2 historical S1 touch gate", "", f"Status: `{result['status']}`.", "",
              f"Old model_fit contains **{model_fit_count:,}** S1; all are within the executed historical model_train labelled audit population of **{counts['model_train']:,}** S1.", "",
              f"Exact eligible set, old model_fit minus historical touches: **{len(eligible_ids):,}** S1. Required minimum: **400,000**. No split allocation or research was performed.", "",
              f"The registry records **{len(registry)}** sources: 583 explicit-S1 artifacts, three executed labelled population audit selectors, {len(broad_operations)} additional broad-read operation scopes, and three corrupt-file conservative supersets.", "",
              f"Historical union: **{touched_count:,}** S1. Old model_fit touched: **{fit_touched:,}**. Model-fit IDs outside old model_train: **{fit_outside_model_train}**.", "",
              "Each registry row records the source file SHA256, explicit S1 ID column, selector, exact distinct-ID count and sorted-ID checksum, prior overlap, new contribution, original model-fit overlap, and provenance. The three corrupt quarantine rows explicitly describe candidate_dev supersets.", "",
              "The full matcher manifest is membership-only evidence and contributes no touches. split_s1/grouped contribute only val; the development split manifest contributes only non-fit roles. The model-fit exclusion is justified by executed labelled-audit evidence, not its role name.", "",
              "Only ID columns and approved split selectors were decoded. Whole-file checksum reads were opaque byte integrity reads. No raw ground truth, test artifacts, V1 outputs, labels, target IDs, features, or scores were decoded. V1 inputs were not changed.", "",
              f"Elapsed: {result['resources']['elapsed_seconds']:.1f} seconds. Unique input bytes hashed: {hashed_bytes:,}. DuckDB: one thread, 512MB limit, V2-only spill directory.", "",
              "Outputs: `work/v2_historical_touch_registry.parquet`, `work/v2_historical_touched_s1.parquet`, `work/v2_eligible_s1.parquet`, `work/v2_historical_touch_checksums.json`.", ""]
    (work / "v2_historical_touch_report.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps({"status": result["status"], "counts": result["counts"], "resources": result["resources"],
                      "checksums": str(checksums_path)}, indent=2), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    run(args.root)


if __name__ == "__main__":
    main()
