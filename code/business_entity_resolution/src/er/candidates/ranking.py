"""Frozen label-free evidence ranking definition."""

RANKER_VERSION = "evidence_ranker_v1"
TARGET_DF_MAX = 2000

EVIDENCE_ORDER_SQL = (
    "both_pass DESC, postal_shared DESC, numeric_shared DESC, "
    "exact_name DESC, exact_addr DESC, round(score,12) DESC, sh DESC, "
    "coverage_s1 DESC, coverage_target DESC, jaccard DESC, mid ASC"
)

EVIDENCE_FIELDS = (
    "both_pass", "postal_shared", "numeric_shared", "exact_name", "exact_addr",
    "round(score,12)", "sh", "coverage_s1", "coverage_target", "jaccard", "mid",
)

HEAVY_ORDER_SQL = (
    "exact_addr DESC, postal_shared DESC, numeric_shared DESC, "
    "exact_name DESC, addr_jaccard DESC, mid ASC"
)


def rank_sql(partition: str) -> str:
    """Return the two deterministic window ranks used by quota policies."""
    return f"""row_number() OVER (PARTITION BY {partition}
        ORDER BY {EVIDENCE_ORDER_SQL})::INT"""
