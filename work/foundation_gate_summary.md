# Foundation review-gate — consolidated evidence (corrected wording)

Status: this is an AUDIT result. It does not "pass because tests are green." Two prior claims
were wrong and are corrected below. No new blocker was implemented. Raw per-item output:
`work/foundation_audit.md`. Precise-wording corrections applied per the review notes.

## Evidence table (claim → command/test → observed → remaining uncertainty)

| # | Claim under audit | Command / test | Observed result | Remaining uncertainty |
|---|---|---|---|---|
| 1 | Recorded figures reproduce | rerun 3 jobs + `unittest discover` | candidates 8,555,794; recall 0.4702; collision keys 55,627; train/val 1,986,290/220,531; **40 tests OK** | none for these |
| 5 | "137 MB" memory | `measure_peak.py` (in-process runpy + `PeakWorkingSetSize`) | 137 MB was **DuckDB internal only**; true **process peak ≈ 600–680 MiB** (integrity 680, split 341, baseline 603, audit 620) | peak varies ±small run-to-run; all < avail RAM |
| 6 | Evaluator vs validator on singletons | official validator subprocess + 3 tests | bare no-tab row → **FAIL** "malformed row (no tab)"; `S1-xxx\t` → PASS. Evaluator now **rejects bare rows** | none |
| 7 | Spec scoring, not validator | `test_spec_worked_example` | F_0.5 macro over ALL S1; empty/empty=1.0, pred-on-singleton=0.0; worked P=2/3,R=1→0.714 ✓ | none; validator is a **format gate only** |
| 4 | Baseline candidate math | materialized distinct join, 1/50 val sample | predicted-by-key = join rows = distinct = **167,861** (exact); exact⊆sorted violations = **0** | sample is 1/50 (deterministic) |
| 4 | sorted key standalone vs union | count-per-key + coverage | standalone recall **0.5061 / 9,060,998**; **exact⊆sorted** (verified) ⇒ union==sorted; **+27,480** GT links, **+505,204** candidates over exact | none |
| 2 | "no leakage" (target≤1 S1) | max addr-Jaccard per rare collision key | overstated. "each S2/S3 ≤1 S1" establishes only the observed **labeled-target uniqueness property**, not absence of entity leakage. On the **probed subset** (42,830 rare keys, ≤5 S1/side, both addr present), **0** have addr Jaccard ≥0.8 | probe covers only that subset; generic-name keys and fuzzy/typo variants untyped |
| 3 | transliteration is needed | script tagging of 404,753 lost pairs | cross-script (S1 latin ↔ target deva) = **28,779 = 7.1%** of lost — **descriptive fraction only, not a formal ceiling** | says nothing about what a translit blocker retrieves |

## Two corrected conclusions
1. **Memory** — the 512 MB DuckDB cap is not a process bound; measured process peak is ~600–680 MiB
   (pyarrow read buffers + Python dominate; baseline's DuckDB was 3.3 MiB yet the process hit 603 MiB).
2. **Split leakage** — "each target ≤1 S1" proves only the observed labeled-target uniqueness property.
   The 46.3% cross-split name-key overlap was probed on a rare-name/both-address subset where
   address corroboration found 0 pairs at Jaccard ≥0.8; this is evidence **for that subset only** and
   is not generalized to all collisions. A grouped name-key holdout is provided **as a distribution-shift
   stress-test diagnostic — NOT a guaranteed lower-bound score**.

## Lost-pair descriptive analysis (denominator = 404,753 lost, or as noted)
These are SIMILARITY/DESCRIPTIVE measurements — similarity opportunity, not demonstrated retrieval.
Conversion of opportunity → actual candidate retrieval is measured in the candidate-generation phase.
- Address similarity opportunity: 96.4% of lost pairs have both addresses; among those,
  **16.1% identical (62,735)** and **48.8% at Jaccard [0.5,1) (190,346)**.
- Name-token overlap: Jaccard [0.5,1) = **27.1% (109,808)**; =1 (=sorted-equal) = **6.8% (27,480)**;
  **zero token overlap = 27.7% (112,058)**.
- Cross-script fraction = **7.1% (28,779)**, India-only (descriptive, not a ceiling).
- 9.4% of lost pairs' S1 get **no exact-key candidate at all**; 90.6% get candidates but miss the true target.

## Next-phase experiment hypotheses (to be MEASURED, not assumed)
Ranking below is a hypothesis list for the candidate-generation phase; nothing here is a proven recall gain.
1. **Address-based blocking** — largest similarity opportunity; must measure actual retrieval conversion,
   block-size tails (shared buildings/malls), and candidate cost.
2. **Name token-overlap blocking** — targets the [0.5,1) name band + sorted-equal; threshold-tunable.
3. **UNION exact ∪ sorted key** — verified superset (exact⊆sorted), +27,480 links / +505,204 candidates.
   Has real (nonzero) candidate-generation and scoring cost; not "free", just analytically subsuming.
- **Transliteration**: 7.1% descriptive cross-script fraction, India-only — defer until simpler bounded
  methods are measured.

## Changed / created files this pass
- `scripts/foundation_audit.py` (new) — items 2/3/4, bounded DuckDB, training-data-only, COPY-guarded.
- `scripts/measure_peak.py` (new) — in-process process-peak probe (item 5).
- `src/er/evaluate.py` (edited) — `read_results_tsv` rejects bare no-tab rows (item 6).
- `tests/fixtures/mini/ground_truth.tsv` (edited) — S1-004 singleton → trailing-tab form.
- `tests/test_fixture.py` (edited) — +3 singleton-representation tests (40 total, all pass).
- `work/foundation_audit.md` (regenerated, corrected wording), `work/split_s1_grouped.parquet`
  (frozen diagnostic; original `work/split_s1.parquet` preserved).
