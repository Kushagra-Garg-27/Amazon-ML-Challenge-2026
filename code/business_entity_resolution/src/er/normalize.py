"""Deterministic, versioned, stdlib-only normalization for business ER.

Pipeline (PRD §16). Every function is pure and reproducible; no external data, no
network, no locale-dependent behavior. Raw strings are never mutated in place — the
caller keeps the originals and stores these as *additional* columns.

Key functions produce blocking/agreement keys:
    name_norm      NFKC -> casefold -> '&'->'and' -> fold Latin accents (keep
                   non-Latin scripts intact) -> keep letters/digits (any script) ->
                   collapse whitespace
    name_nosuffix  name_norm minus trailing/standalone legal-suffix tokens
    name_sorted    tokens of name_nosuffix, deduped-order-invariant (sorted+joined)
    name_acronym   first char of each name_nosuffix token (>=2 tokens), else ""
    addr_norm      same char pipeline as name_norm (street words kept for recall)
    num_tokens     sorted distinct digit-groups in the address (PIN/building nos.)
    country_norm   trimmed casefold label — OPEN SET, no mapping/one-hot

Cross-script transliteration (Devanagari<->Latin) is intentionally NOT done here:
this baseline keeps scripts separate; a translit key is a later, separately measured
experiment (PRD §24). ``fold_accents`` deliberately preserves Devanagari matras.
"""
from __future__ import annotations

import unicodedata
from typing import Dict, List

from er.resources.suffixes import LEGAL_SUFFIXES

NORM_VERSION = "1"


def fold_accents(s: str) -> str:
    """Drop diacritics from Latin letters only; preserve other scripts' marks.

    café -> cafe, naïve -> naive, but Devanagari matras (combining marks on an
    Indic base) are kept so Hindi names are not corrupted.
    """
    res: List[str] = []
    prev_latin = False
    for ch in unicodedata.normalize("NFD", s):
        if unicodedata.combining(ch):
            if not prev_latin:
                res.append(ch)  # keep matra / non-Latin mark
            continue
        prev_latin = "LATIN" in unicodedata.name(ch, "")
        res.append(ch)
    return unicodedata.normalize("NFC", "".join(res))


def _keep_alnum_any_script(s: str) -> str:
    """Keep Unicode letters/numbers/marks (any script); other chars -> space.

    Marks (category M*) are kept so Indic vowel signs (Devanagari matras) survive;
    Latin diacritics were already folded away by ``fold_accents`` before this runs.
    """
    out = []
    for ch in s:
        out.append(ch if unicodedata.category(ch)[0] in ("L", "N", "M") else " ")
    return "".join(out)


def name_norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.casefold()
    s = s.replace("&", " and ").replace("＆", " and ")
    s = fold_accents(s)
    s = _keep_alnum_any_script(s)
    return " ".join(s.split())


def _tokens_no_suffix(norm: str) -> List[str]:
    toks = [t for t in norm.split() if t not in LEGAL_SUFFIXES]
    return toks if toks else norm.split()  # never return empty if name was all-suffix


def name_nosuffix(s: str) -> str:
    return " ".join(_tokens_no_suffix(name_norm(s)))


def name_sorted(s: str) -> str:
    return " ".join(sorted(set(_tokens_no_suffix(name_norm(s)))))


def name_acronym(s: str) -> str:
    toks = _tokens_no_suffix(name_norm(s))
    return "".join(t[0] for t in toks) if len(toks) >= 2 else ""


def addr_norm(s: str) -> str:
    return name_norm(s)  # same char pipeline; street words retained for recall


def num_tokens(s: str) -> List[str]:
    if not s:
        return []
    digits, cur = [], []
    for ch in s:
        if ch.isdigit():
            cur.append(ch)
        elif cur:
            digits.append("".join(cur))
            cur = []
    if cur:
        digits.append("".join(cur))
    return sorted(set(digits))


def country_norm(s: str) -> str:
    """Open-set country label: trim + casefold only. No mapping, no one-hot."""
    return (s or "").strip().casefold()


def normalize_record(name: str, address: str, country: str) -> Dict[str, object]:
    """All keys for one source row. Raw inputs are not modified."""
    nn = name_norm(name)
    toks = _tokens_no_suffix(nn)
    nosfx = " ".join(toks)
    return {
        "norm_version": NORM_VERSION,
        "name_norm": nn,
        "name_nosuffix": nosfx,
        "name_sorted": " ".join(sorted(set(toks))),
        "name_acronym": "".join(t[0] for t in toks) if len(toks) >= 2 else "",
        "addr_norm": addr_norm(address),
        "num_tokens": num_tokens(address),
        "country_norm": country_norm(country),
    }
