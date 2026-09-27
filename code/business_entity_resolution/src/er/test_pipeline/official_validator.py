#!/usr/bin/env python3
"""
ML Challenge 2026 — Submission Validator

Run this BEFORE submitting. It checks your output files against every formatting
rule the scorer enforces, so you can catch a rejection locally instead of burning
a submission. It reads only your output files and the test source files (to learn
which S1 entities are required and which S2/S3 IDs exist); it never needs the
ground truth and never computes your score.

It validates two files:

* ``matching_results.tsv`` (required) — your final matches, the file scored on the
  leaderboard.
* ``candidate_pairs.tsv`` (optional) — the candidate set from your blocking stage.
  When present, the validator also checks that your final matches are a subset of
  your candidates and *warns* (never fails) otherwise. When absent it is skipped
  with a warning; it is still expected in your final submission zip.

Stdlib only, Python 3.8+. Run from the ``student_resource/`` directory::

    python3 utils/validate_submission.py \
        --matching output/matching_results.tsv \
        --candidate output/candidate_pairs.tsv \
        --test-dir dataset/test

Exit code 0 means the files are safe to submit; 1 means fix the listed issues
(warnings never fail the run).

ID-existence check (off by default). By default the validator does NOT check that
every matched/candidate ID actually exists in the test set: that check loads all
Source-2/3 IDs into memory, which on the full ~1.7M-entity test set costs a few GB
(more when ``candidate_pairs.tsv`` is included). The default run therefore stays fast
and light and verifies every other rule; it prints a warning noting the check was
skipped. Pass ``--check-ids`` to turn it on (it reads ``test_source2.tsv`` /
``test_source3.tsv`` from ``--test-dir``); a missing/garbage matched ID only lowers
your score rather than being rejected by the scorer, so this check is a diagnostic,
not a gate. If ``--check-ids`` runs out of memory, drop ``--candidate`` (the candidate
cross-check is the biggest memory user, and the matching file is the only one scored).
"""

import argparse
import os
import sys
from itertools import zip_longest

DELIM = "\t"
MAX_EXAMPLES = 5  # how many offending IDs to show per issue
MATCHING_HEADER = ["source1_entity_id", "matched_entity_ids"]
CANDIDATE_HEADER = ["source1_entity_id", "candidate_entity_ids"]


def read_ids(path):
    """Return the set of first-column entity IDs from a source TSV.

    The header row is skipped and blank lines are ignored.
    """
    with open(path, encoding="utf-8") as f:
        next(f, None)  # skip header
        return {line.split(DELIM, 1)[0].strip() for line in f if line.strip()}


def examples(items):
    """Return a short, human-readable sample of ``items`` for an error message."""
    items = sorted(items)
    shown = ", ".join(items[:MAX_EXAMPLES])
    if len(items) > MAX_EXAMPLES:
        return f"{len(items)} total, e.g. {shown}, ..."
    return shown


def load_match_targets(test_dir, warnings):
    """Return the set of valid S2/S3 match IDs, or ``None`` if unavailable.

    Only called when ``--check-ids`` is on. When ``test_source2.tsv`` or
    ``test_source3.tsv`` is missing we cannot check that matched IDs exist, so we
    record a warning and return ``None`` to signal that the existence check should be
    skipped.
    """
    targets = set()
    for name in ("test_source2.tsv", "test_source3.tsv"):
        path = os.path.join(test_dir, name)
        if not os.path.isfile(path):
            warnings.append(
                f"{path} not found — skipping the (optional) check that matched "
                f"IDs exist in the test set. Every other rule is still checked. "
                f"This is the lighter-memory mode; provide test_source2/3.tsv to "
                f"enable the ID-existence check."
            )
            return None
        targets |= read_ids(path)
    return targets


def validate_id_list_file(path, expected_header, col_label, required, valid_ids, errors):
    """Validate one results-style TSV (matching or candidate).

    Applies the shared formatting rules and appends any problems to ``errors``.
    Returns a ``{source1_id: set(matched/candidate ids)}`` mapping, or ``None`` on a
    fatal problem (missing file, empty file, or a broken header) that stops parsing.
    """
    if not os.path.isfile(path):
        errors.append(f"File not found: {path}")
        return None

    name = os.path.basename(path)
    mapping = {}
    seen, dup_rows, intra_dupes = set(), set(), set()
    self_matches, wrong_prefix, unknown = set(), set(), set()
    n_rows = empties = 0

    with open(path, encoding="utf-8") as f:
        header = f.readline()
        if not header:
            errors.append(f"{name} is empty.")
            return None
        if DELIM not in header and "," in header:  # the #1 mistake: a CSV
            errors.append(
                f"{name}: header has no TAB but contains commas — the file looks "
                "COMMA-separated. Submissions must be TAB-separated (.tsv); "
                "write it with df.to_csv(sep='\\t', index=False)."
            )
            return None
        cols = [c.strip().lower() for c in header.rstrip("\n").split(DELIM)]
        if cols != expected_header:
            errors.append(
                f"{name}: unexpected header {cols}. "
                f"Expected exactly {expected_header} (tab-separated)."
            )
            return None

        for line_num, line in enumerate(f, start=2):
            s1, tab, rest = line.partition(DELIM)
            if not tab:
                if s1.strip():
                    errors.append(
                        f"{name}: malformed row (no tab) at line {line_num}: "
                        f"{line.rstrip()!r}"
                    )
                continue

            n_rows += 1
            if s1 in seen:
                dup_rows.add(s1)
            seen.add(s1)

            ids = rest.rstrip("\n").split(",") if rest.strip() else []
            if not ids:
                empties += 1
                mapping[s1] = set()
                continue
            if len(ids) != len(set(ids)):
                intra_dupes.add(s1)
            id_set = set(ids)
            mapping[s1] = id_set
            for mid in id_set:
                if mid.startswith("S1-"):
                    self_matches.add(mid)
                elif not mid.startswith(("S2-", "S3-")):
                    wrong_prefix.add(mid)
                elif valid_ids is not None and mid not in valid_ids:
                    unknown.add(mid)

    # Aggregate the per-category findings. Each entry is (offenders, message);
    # only non-empty categories become errors.
    findings = [
        (
            dup_rows,
            "{name}: duplicate source1_entity_id row(s): {ex}. "
            "Each S1 entity may appear on only one row.",
        ),
        (
            intra_dupes,
            "{name}: repeated ID inside a {col} list for: {ex}. "
            "No duplicate IDs are allowed within a list.",
        ),
        (
            self_matches,
            "{name}: {col} contains Source-1 IDs (self-matches): {ex}. "
            "Only S2-/S3- IDs are allowed.",
        ),
        (
            wrong_prefix,
            "{name}: {col} contains IDs without an S2-/S3- prefix: {ex}.",
        ),
        (
            unknown,
            "{name}: {col} references IDs not in the test "
            "Source-2/3 files: {ex}.",
        ),
        (
            required - seen,
            "{name}: required S1 entity(ies) missing: {ex}. "
            "Every entity in test_source1.tsv needs a row (empty = no match).",
        ),
        (
            seen - required,
            "{name}: row(s) using an S1 ID that is not in the test set: {ex}.",
        ),
    ]
    for offenders, message in findings:
        if offenders:
            errors.append(message.format(name=name, ex=examples(offenders), col=col_label))

    print(f"  {name}: {n_rows} rows ({empties} empty, {n_rows - empties} non-empty).")
    return mapping


def validate(matching_path, candidate_path, test_dir, check_ids=False):
    """Validate the submission output(s); return ``(errors, warnings)`` lists.

    ``check_ids`` (``--check-ids``) turns on the optional, memory-heavy check that
    every matched/candidate ID exists in the test Source-2/3 files. It is off by
    default so the common run stays fast and light.
    """
    errors, warnings = [], []

    source1 = os.path.join(test_dir, "test_source1.tsv")
    if not os.path.isfile(source1):
        errors.append(f"Test source1 file not found: {source1} (check --test-dir).")
        return errors, warnings
    required = read_ids(source1)
    print(f"  required S1 entities: {len(required)}")

    if check_ids:
        valid_ids = load_match_targets(test_dir, warnings)
        if valid_ids is not None:
            print(f"  valid S2/S3 match IDs: {len(valid_ids)}")
    else:
        valid_ids = None
        warnings.append(
            "ID-existence check is OFF (the default) — not checking that matched/"
            "candidate IDs exist in the test set. Every other rule is still checked. "
            "Re-run with --check-ids to enable it (needs test_source2/3.tsv; uses "
            "more memory). A nonexistent ID only lowers your score, never rejects "
            "your submission."
        )

    matched = validate_id_list_file(
        matching_path, MATCHING_HEADER, "matched_entity_ids", required, valid_ids, errors
    )

    # candidate_pairs.tsv is optional: if it's absent we skip its checks with a
    # warning (it's still expected in your final submission zip). A missing
    # candidate file never fails this run on its own.
    candidate = None
    if candidate_path and os.path.isfile(candidate_path):
        candidate = validate_id_list_file(
            candidate_path, CANDIDATE_HEADER, "candidate_entity_ids",
            required, valid_ids, errors,
        )
    elif candidate_path:
        warnings.append(
            f"{candidate_path} not found — skipping candidate_pairs.tsv checks. "
            "It is optional here, but your final submission zip must include "
            "output/candidate_pairs.tsv."
        )

    # Soft check: your final matches should come from your blocking candidates.
    # A matched ID absent from candidate_pairs.tsv usually means a pipeline bug,
    # so we warn but never fail on it.
    if matched is not None and candidate is not None:
        offenders = {
            s1 for s1, mids in matched.items() if mids - candidate.get(s1, set())
        }
        if offenders:
            warnings.append(
                f"{len(offenders)} S1 entity(ies) have matched IDs not present in "
                f"candidate_pairs.tsv, e.g. {examples(offenders)}. Final matches "
                "normally come from your blocking candidates — double-check these."
            )

    return errors, warnings


def validate_large_streaming(matching_path, candidate_path, test_dir, check_ids=False):
    """Memory-bounded equivalent of the challenge checks for very large outputs.

    The release files are sorted by raw S1 ID. Holding 271M candidate IDs in the
    original in-memory mapping would exceed the guarded machine's RAM. This path
    keeps only one S1 pair of lists at a time. It also enforces exact TSV syntax.
    """
    errors, warnings = [], []
    source1 = os.path.join(test_dir, "test_source1.tsv")
    if not os.path.isfile(source1):
        return [f"Test source1 file not found: {source1}"], warnings
    if not os.path.isfile(matching_path) or not os.path.isfile(candidate_path):
        return ["Matching and candidate files are both required for large-output validation."], warnings
    remaining = read_ids(source1)
    expected_count = len(remaining)
    print(f"  required S1 entities: {expected_count}")
    if check_ids:
        valid_ids = load_match_targets(test_dir, warnings)
        if valid_ids is not None:
            print(f"  valid S2/S3 match IDs: {len(valid_ids)}")
    else:
        valid_ids = None
        warnings.append("ID-existence check is OFF (the default) — re-run with --check-ids to enable it.")
    matching_rows = candidate_rows = matching_empty = candidate_empty = 0
    previous = None
    with open(matching_path, encoding="utf-8", newline="") as mf, open(candidate_path, encoding="utf-8", newline="") as cf:
        if mf.readline() != "source1_entity_id\tmatched_entity_ids\n":
            return ["matching_results.tsv: unexpected header; expected exact lowercase tab-separated header."], warnings
        if cf.readline() != "source1_entity_id\tcandidate_entity_ids\n":
            return ["candidate_pairs.tsv: unexpected header; expected exact lowercase tab-separated header."], warnings
        for line_number, (mline, cline) in enumerate(zip_longest(mf, cf), start=2):
            if mline is None or cline is None:
                errors.append(f"Output row count differs between matching and candidate at line {line_number}.")
                break
            if not mline.endswith("\n") or not cline.endswith("\n") or mline.count("\t") != 1 or cline.count("\t") != 1:
                errors.append(f"Malformed row at line {line_number}: expected exactly two tab-separated fields.")
                break
            ms1, mvalues = mline[:-1].split("\t")
            cs1, cvalues = cline[:-1].split("\t")
            if ms1 != cs1:
                errors.append(f"S1 identity differs between files at line {line_number}.")
                break
            if previous is not None and ms1 <= previous:
                errors.append(f"S1 rows are duplicate or unordered at line {line_number}.")
                break
            previous = ms1
            if ms1 not in remaining:
                errors.append(f"Unknown or duplicate S1 entity {ms1!r} at line {line_number}.")
                break
            remaining.remove(ms1)
            mids = mvalues.split(",") if mvalues else []
            cids = cvalues.split(",") if cvalues else []
            for label, values in (("matched", mids), ("candidate", cids)):
                if len(values) != len(set(values)):
                    errors.append(f"Repeated ID inside {label} list at line {line_number}.")
                    break
                for target in values:
                    if not target.startswith(("S2-", "S3-")) or target.startswith("S1-") or " " in target or "\r" in target:
                        errors.append(f"Invalid {label} target ID {target!r} at line {line_number}.")
                        break
                    if valid_ids is not None and target not in valid_ids:
                        errors.append(f"Unknown test S2/S3 target {target!r} at line {line_number}.")
                        break
                if errors:
                    break
            if errors:
                break
            if not set(mids).issubset(cids):
                warnings.append(f"Matches outside candidates at line {line_number}.")
                errors.append("Matches must be a subset of final candidates.")
                break
            matching_rows += 1
            candidate_rows += 1
            matching_empty += not mids
            candidate_empty += not cids
    if not errors and remaining:
        errors.append(f"{len(remaining)} required S1 entities missing from both output files, e.g. {examples(remaining)}")
    if not errors and matching_rows != expected_count:
        errors.append(f"Expected {expected_count} output rows, found {matching_rows}.")
    print(f"  matching_results.tsv: {matching_rows} rows ({matching_empty} empty, {matching_rows-matching_empty} non-empty).")
    print(f"  candidate_pairs.tsv: {candidate_rows} rows ({candidate_empty} empty, {candidate_rows-candidate_empty} non-empty).")
    return errors, warnings


def main():
    parser = argparse.ArgumentParser(
        description="Validate ML Challenge 2026 submission output files before submitting."
    )
    parser.add_argument(
        "--matching",
        "-m",
        default="output/matching_results.tsv",
        help="Path to matching_results.tsv (default: %(default)s)",
    )
    parser.add_argument(
        "--candidate",
        "-c",
        default=None,
        help="Path to candidate_pairs.tsv "
        "(default: output/candidate_pairs.tsv if it exists).",
    )
    parser.add_argument(
        "--test-dir",
        "-t",
        default="dataset/test",
        help="Folder with test_source1/2/3.tsv (default: %(default)s). "
        "test_source2/3.tsv are only read when --check-ids is given.",
    )
    parser.add_argument(
        "--check-ids",
        action="store_true",
        help="Also check that every matched/candidate ID exists in the test "
        "Source-2/3 files. Off by default (loads all S2/S3 IDs into memory — a few "
        "GB on the full test set). A nonexistent ID only lowers your score, so this "
        "is a diagnostic, not a submission gate.",
    )
    args = parser.parse_args()

    # candidate_pairs.tsv is optional; default to the conventional path and let
    # validate() skip (with a warning) if the file isn't there.
    candidate_path = args.candidate or "output/candidate_pairs.tsv"

    print("ML Challenge 2026 — submission validator")
    print(f"  test dir: {args.test_dir}")
    try:
        # The challenge's original dictionary implementation holds every target
        # list in RAM. Full release candidates are too large for that machine.
        # Preserve the original path for bounded fixtures; stream full outputs.
        validator = (validate_large_streaming if candidate_path and os.path.isfile(candidate_path)
                     and os.path.getsize(candidate_path) > 256_000_000 else validate)
        errors, warnings = validator(args.matching, candidate_path, args.test_dir,
                                     check_ids=args.check_ids)
    except UnicodeDecodeError:
        print()
        print("FAIL — 1 issue(s) to fix before submitting:")
        print(
            f"  1. A file is not valid UTF-8 text (most likely {args.matching} or "
            f"{candidate_path}). Re-save it as a plain UTF-8, tab-separated .tsv — "
            "not cp1252/Latin-1, and not a compressed or binary file (.gz/.xlsx/"
            ".parquet) renamed to .tsv. In pandas: "
            "df.to_csv(path, sep='\\t', index=False, encoding='utf-8')."
        )
        return 1
    except OSError as exc:
        print()
        print("FAIL — 1 issue(s) to fix before submitting:")
        print(f"  1. Could not read a file: {exc}.")
        return 1

    print()
    for warning in warnings:
        print(f"WARNING: {warning}")
    if errors:
        print(f"FAIL — {len(errors)} issue(s) to fix before submitting:")
        for i, error in enumerate(errors, 1):
            print(f"  {i}. {error}")
        return 1
    print("PASS — no blocking issues found. Safe to submit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
