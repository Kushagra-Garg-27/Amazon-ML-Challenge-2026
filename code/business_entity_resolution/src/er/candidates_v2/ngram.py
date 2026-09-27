"""Deterministic normalized-name character n-grams; label-free at inference."""
from __future__ import annotations

def grams(value: str, n: int) -> tuple[str, ...]:
    if n not in (2,3,4):
        raise ValueError('Only independently profiled lengths 2, 3 and 4 are supported')
    if not value or len(value) < n or len(value) > 64:
        return ()
    return tuple(sorted({value[i:i+n] for i in range(len(value)-n+1)}))


def duckdb_grams_sql(name_column: str, n: int) -> str:
    if name_column not in {'name_norm','nm'} or n not in (2,3,4):
        raise ValueError('Unreviewed SQL n-gram parameters')
    return (f"unnest(list_distinct(list_transform(range(1,length({name_column})-{n}+2), "
            f"i -> substr({name_column},i,{n}))))")
