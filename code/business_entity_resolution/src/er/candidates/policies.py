"""Versioned deterministic candidate policy schema and validation."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


DYNAMIC_K_SQL = (
    "CASE WHEN coalesce(b.n,0)>0 THEN 25 "
    "WHEN coalesce(a.n,0)>0 OR coalesce(n.n,0) BETWEEN 1 AND 5 THEN 50 "
    "ELSE 75 END"
)


@dataclass(frozen=True)
class CandidatePolicy:
    policy_id: str
    ranker_version: str
    name_df_max: int
    address_df_max: int
    s2_quota: int
    s3_quota: int
    heavy_sorted_threshold: int
    heavy_sorted_cap: int | None
    partitions: int
    provenance_bits: dict[str, int]
    output_order: tuple[str, ...]

    def validate(self) -> None:
        if self.ranker_version != "evidence_ranker_v1":
            raise ValueError(f"Unsupported ranker: {self.ranker_version}")
        if min(self.s2_quota, self.s3_quota, self.partitions) <= 0:
            raise ValueError("Quotas and partitions must be positive")
        if self.heavy_sorted_cap is not None and self.heavy_sorted_cap <= 0:
            raise ValueError("Heavy cap must be positive or null")
        if self.provenance_bits != {
            "sorted_name": 1, "exact_address": 2,
            "name_token": 4, "address_token": 8, "sister_expansion": 16,
        }:
            raise ValueError("Unexpected provenance schema")
        if self.output_order != ("source1_entity_id", "target_entity_id"):
            raise ValueError("Output order must be stable identity order")


def load_policy(path: str | Path) -> CandidatePolicy:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    p = raw["policy"]
    policy = CandidatePolicy(
        policy_id=p["policy_id"], ranker_version=p["ranker_version"],
        name_df_max=p["df_thresholds"]["name"],
        address_df_max=p["df_thresholds"]["address"],
        s2_quota=p["source_quotas"]["S2"], s3_quota=p["source_quotas"]["S3"],
        heavy_sorted_threshold=p["heavy_sorted"]["target_block_min"],
        heavy_sorted_cap=p["heavy_sorted"]["cap"],
        partitions=p["partitioning"]["hash_partitions"],
        provenance_bits=p["provenance_bits"], output_order=tuple(p["output_order"]),
    )
    policy.validate()
    return policy


def source_quota_predicate(policy: CandidatePolicy, alias: str = "r") -> str:
    return (f"(({alias}.mid LIKE 'S2-%' AND {alias}.rsource<={policy.s2_quota}) OR "
            f"({alias}.mid LIKE 'S3-%' AND {alias}.rsource<={policy.s3_quota}))")
