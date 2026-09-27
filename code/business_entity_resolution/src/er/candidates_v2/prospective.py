"""Amended eligibility and prospective label boundary; no automatic label opening."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable

from .firewall import REQUESTED, canonical_ids

SEED = 'v2_prospective_20260927_rule_amendment_1'
CATEGORIES = {'MEMBERSHIP_ONLY', 'AGGREGATE_ONLY', 'DECISION_RELEVANT_ROW_LEVEL', 'AMBIGUOUS'}


def excludes(category: str) -> bool:
    if category not in CATEGORIES:
        raise ValueError('Unknown exposure category; refusing eligibility decision')
    return category in {'DECISION_RELEVANT_ROW_LEVEL', 'AMBIGUOUS'}


def assign(ids: Iterable[str], seed: str = SEED) -> list[tuple[str, str]]:
    ids = canonical_ids(ids)
    if len(ids) < sum(REQUESTED.values()):
        raise ValueError(f'Insufficient pool: shortage {sum(REQUESTED.values())-len(ids)}')
    ranked = sorted(ids, key=lambda value: (hashlib.sha256((seed + '\0' + value).encode()).digest(), value))
    result, start = [], 0
    for name, count in REQUESTED.items():
        result.extend((entity, name) for entity in ranked[start:start+count])
        start += count
    result.extend((entity, 'v2_matcher_train') for entity in ranked[start:])
    return sorted(result)


def assert_research_only(ids: Iterable[str], research: set[str], sealed: set[str]) -> None:
    ids = set(ids)
    if research & sealed:
        raise PermissionError('Research and prospectively sealed populations overlap')
    if ids & sealed or not ids <= research:
        raise PermissionError('Non-research S1 in a label-derived operation')


def scoped_gt_rows(lines: Iterable[bytes], research: set[str], sealed: set[str]):
    """Decode target labels only AFTER exact S1 membership has passed the firewall.

    The TSV is physically streamed. Unselected label bytes are never decoded,
    split into targets, retained, counted or exposed. This is an explicit
    projection limitation of the source format, recorded in the access ledger.
    """
    assert_research_only(research, research, sealed)
    iterator = iter(lines)
    header = next(iterator).rstrip(b'\r\n').split(b'\t')
    if header != [b'source1_entity_id', b'matched_entity_ids']:
        raise ValueError('Unexpected GT schema')
    seen = set()
    for raw in iterator:
        identity, separator, remainder = raw.partition(b'\t')
        if not separator:
            raise ValueError('Malformed GT row')
        entity = identity.decode('utf-8')
        if entity not in research:
            continue
        # The disjointness check above applies to the complete frozen sets.
        # Membership here is the per-row gate before target bytes are decoded.
        if entity in seen:
            raise ValueError('Duplicate research GT identity')
        seen.add(entity)
        targets = tuple(sorted({target.strip() for target in remainder.decode('utf-8').strip().split(',') if target.strip()}))
        yield entity, targets
    if seen != research:
        raise ValueError('Missing research GT rows')
