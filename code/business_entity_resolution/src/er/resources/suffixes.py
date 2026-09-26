"""Legal-suffix and organizational tokens (generic, multi-locale, deterministic).

Country-agnostic corporate-form words used to derive ``name_nosuffix``. This is NOT
an entity lookup or external enrichment (fair-play safe) — just common legal/org
suffix words. Extend generically; never add specific business names or hard-code a
country's entities.
"""

# Genuine legal/incorporation forms only. Descriptor words (international, group,
# holdings, industries, enterprises, the, ...) are deliberately NOT included: they
# carry signal and stripping them over-merges distinct businesses (hurts precision,
# which F_0.5 weights 2x). "and" is included so "Barnes & Noble" -> "barnes noble".
LEGAL_SUFFIXES = frozenset({
    # English / generic
    "ltd", "limited", "llc", "llp", "inc", "incorporated", "corp", "corporation",
    "co", "company", "plc", "and",
    # India
    "pvt", "private",
    # France / EU
    "sarl", "sa", "sas", "sasu", "sci", "snc", "eurl", "gmbh", "ag", "bv", "nv",
    "srl", "spa", "sl", "oy", "ab", "as", "kg", "kft", "ug", "kgaa",
    # Asia / other
    "pte", "sdn", "bhd", "kk", "gk",
})

# Kept for later address features / blocking; NOT stripped by the baseline addr_norm.
STREET_TYPES = frozenset({
    "street", "st", "road", "rd", "avenue", "ave", "lane", "ln", "boulevard",
    "blvd", "drive", "dr", "suite", "ste", "floor", "fl", "building", "bldg",
    "block", "sector", "phase", "marg", "nagar", "cross", "main",
})
