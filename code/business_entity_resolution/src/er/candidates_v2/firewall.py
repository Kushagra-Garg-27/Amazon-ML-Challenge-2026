"""ID-only, fail-closed entry gate. This module has no label reader."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping


REQUESTED = {
    "v2_candidate_research": 100_000,
    "v2_candidate_holdout": 100_000,
    "v2_matcher_tune": 50_000,
    "v2_threshold": 50_000,
    "v2_final_eval": 100_000,
}
PROTECTION_ORDER = (
    "v2_final_eval", "v2_candidate_holdout", "v2_threshold",
    "v2_matcher_tune", "v2_candidate_research", "v2_matcher_train",
)


def canonical_ids(ids: Iterable[str]) -> tuple[str, ...]:
    values = set(ids)
    if any(not isinstance(value, str) or not value or "\n" in value or "\r" in value
           for value in values):
        raise ValueError("S1 IDs must be nonempty strings without line separators")
    return tuple(sorted(values))


def id_checksum(ids: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for value in canonical_ids(ids):
        digest.update((value + "\n").encode("utf-8"))
    return digest.hexdigest()


def eligible_ids(old_model_fit: Iterable[str], touched: Iterable[str]) -> tuple[str, ...]:
    """Membership in old_model_fit is not itself an exclusion source."""
    return tuple(sorted(set(canonical_ids(old_model_fit)) - set(canonical_ids(touched))))


def allocation_gate(eligible_count: int) -> dict:
    if not isinstance(eligible_count, int) or isinstance(eligible_count, bool) or eligible_count < 0:
        raise ValueError("eligible_count must be a nonnegative integer")
    required = sum(REQUESTED.values())
    return {
        "status": "BLOCKED_INSUFFICIENT_POOL" if eligible_count < required else "READY_FOR_SPLIT",
        "eligible_s1": eligible_count,
        "requested_counts": dict(REQUESTED),
        "minimum_required_s1": required,
        "shortage_s1": max(0, required - eligible_count),
        "protection_priority": list(PROTECTION_ORDER),
        "allocation_performed": False,
        "actual_counts": None,
        "label_access_authorized": False,
    }


def require_research_access(gate: Mapping) -> None:
    """Neither sufficient capacity nor an empty placeholder grants label access.

    A later separately implemented split/label interface must establish the
    complete firewall and candidate audit before any label reader exists.
    """
    raise PermissionError(
        f"V2 label access is closed: {gate.get('status', 'UNVERIFIED')}; "
        "this gate-only phase has no authorized label reader"
    )
