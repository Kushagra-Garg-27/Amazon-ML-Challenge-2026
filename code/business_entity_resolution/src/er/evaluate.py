"""Entity-level macro-F_0.5 evaluator — the challenge's *scoring* semantics.

The official ``utils/validate_submission.py`` only checks output *format*; it never
computes a score and never reads ground truth. This module implements the score the
leaderboard uses, per the spec (ProblemStatement.txt lines 128-133, 208-209 /
CHALLENGE.md lines 195-209):

    F_0.5 = (1 + 0.5^2)·P·R / (0.5^2·P + R) = 1.25·P·R / (0.25·P + R)

computed per Source-1 entity then **macro-averaged over ALL S1 entities in the
evaluation set (singletons included)**.

Degenerate-case convention (the spec fixes the singleton rows explicitly; the rest
are the standard IR convention, isolated here behind one tested function so the
assumption is auditable, not scattered):

    truth=∅, pred=∅            -> 1.0   (spec: correct singleton)
    truth=∅, pred≠∅            -> 0.0   (spec: any merge on a true singleton)
    truth≠∅, pred=∅            -> 0.0   (recall 0; nothing predicted)
    truth≠∅, pred≠∅, TP=0      -> 0.0   (disjoint; P=R=0, denominator 0 -> define 0)
    otherwise                  -> 1.25·P·R/(0.25·P+R)

Worked example from the spec (line 208): pred={S2-00047,S2-00193,S3-00812},
truth={S2-00047,S3-00812} -> P=2/3, R=1.0, F_0.5=0.714 (see tests/test_evaluate.py).

Micro pair precision/recall are provided as *diagnostics only* — they are NOT the
leaderboard metric and must never be reported in its place.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional, Set

BETA = 0.5
_B2 = BETA * BETA          # 0.25
_ONE_PLUS_B2 = 1.0 + _B2   # 1.25
EVAL_VERSION = "1"         # bump if the scoring convention ever changes


def f_beta_entity(pred: Set[str], truth: Set[str]) -> float:
    """F_0.5 for a single S1 entity. See module docstring for the convention."""
    if not truth:
        return 1.0 if not pred else 0.0
    if not pred:
        return 0.0
    tp = len(pred & truth)
    if tp == 0:
        return 0.0
    p = tp / len(pred)
    r = tp / len(truth)
    return _ONE_PLUS_B2 * p * r / (_B2 * p + r)


def macro_f_beta(
    pred: Dict[str, Set[str]],
    truth: Dict[str, Set[str]],
    entities: Optional[Iterable[str]] = None,
) -> float:
    """Macro-average of ``f_beta_entity`` over the S1 universe.

    ``entities`` is the full set of S1 ids to average over; it MUST include
    singletons. Default = keys of ``truth`` (the GT has one row per S1, empty or
    not), which is the correct universe when scoring against full ground truth.
    """
    keys = set(truth.keys()) if entities is None else set(entities)
    if not keys:
        return 0.0
    total = 0.0
    for k in keys:
        total += f_beta_entity(pred.get(k, set()), truth.get(k, set()))
    return total / len(keys)


def micro_pair_pr(pred: Dict[str, Set[str]], truth: Dict[str, Set[str]]) -> dict:
    """DIAGNOSTIC ONLY: micro-averaged pairwise precision/recall over all links."""
    tp = fp = fn = 0
    for k in set(pred) | set(truth):
        p, t = pred.get(k, set()), truth.get(k, set())
        tp += len(p & t)
        fp += len(p - t)
        fn += len(t - p)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "pairs_tp": tp, "pairs_fp": fp, "pairs_fn": fn,
        "micro_precision": prec, "micro_recall": rec,
    }


def report(
    pred: Dict[str, Set[str]],
    truth: Dict[str, Set[str]],
    entities: Optional[Iterable[str]] = None,
    country: Optional[Dict[str, str]] = None,
) -> dict:
    """Full scoring report: primary macro-F_0.5 + per-entity mean P/R + micro diag."""
    keys = set(truth.keys()) if entities is None else set(entities)
    n = len(keys)
    sum_f = sum_p = sum_r = 0.0
    n_singleton_true = n_singleton_correct = 0
    per_country_f: Dict[str, list] = {}
    for k in keys:
        p, t = pred.get(k, set()), truth.get(k, set())
        f = f_beta_entity(p, t)
        sum_f += f
        if not t:
            n_singleton_true += 1
            if not p:
                n_singleton_correct += 1
        tp = len(p & t)
        sum_p += (tp / len(p)) if p else (1.0 if not t else 0.0)
        sum_r += (tp / len(t)) if t else 1.0
        if country is not None:
            per_country_f.setdefault(country.get(k, "?"), []).append(f)
    out = {
        "eval_version": EVAL_VERSION,
        "n_entities": n,
        "macro_f0_5": (sum_f / n) if n else 0.0,
        "mean_entity_precision": (sum_p / n) if n else 0.0,
        "mean_entity_recall": (sum_r / n) if n else 0.0,
        "n_singleton_true": n_singleton_true,
        "n_singleton_correct": n_singleton_correct,
    }
    out.update(micro_pair_pr(pred, truth))
    if country is not None:
        out["per_country_macro_f0_5"] = {
            c: sum(v) / len(v) for c, v in sorted(per_country_f.items())
        }
    return out


def read_results_tsv(path: str) -> Dict[str, Set[str]]:
    """Parse a matching/candidate/GT TSV into {s1_id: set(ids)}.

    Splits the id list on ',' with NO whitespace stripping, exactly like
    ``utils/validate_submission.py`` (so self-scoring sees the same bytes the official
    scorer would).

    Malformed-row policy is aligned to the official validator (verified 2026-09-25):
    the validator rejects a row with NO TAB as ``malformed row (no tab)`` and does not
    admit its S1 id. A singleton is therefore encoded ``S1-xxx\\t`` — an explicit
    trailing tab with an empty second field — which is exactly how the real
    ``train_ground_truth.tsv`` writes its 123,247 singleton rows (0 bare rows observed).
    We raise on a bare (no-tab) non-empty row so this reader never scores a file the
    validator would reject; a ``S1-xxx\\t`` row parses as an empty match set.
    """
    out: Dict[str, Set[str]] = {}
    with open(path, encoding="utf-8") as f:
        f.readline()  # header
        for line_num, line in enumerate(f, start=2):
            line = line.rstrip("\n")
            if not line:
                continue
            s1, tab, rest = line.partition("\t")
            if not tab:
                raise ValueError(
                    f"{path}:{line_num}: malformed row (no TAB) {line!r} — the "
                    f"official validator rejects this. Write a singleton as "
                    f"'S1-xxx\\t' (trailing tab, empty field)."
                )
            out[s1] = set(rest.split(",")) if rest.strip() else set()
    return out
