# AMAZAON ML Challenge — Entity Resolution

> **Status:** V2 Research / Candidate-Generation Phase
> **Objective:** Build a high-precision, high-F₀.₅ entity-resolution system for matching S1 records against S2/S3 records.
> **Primary optimization target:** **Macro F₀.₅**
> **Core principle:** Precision first, but without sacrificing recoverable true matches.

---

## 1. Project Overview

This project solves an **entity-resolution / record-linkage problem** in which records from a source dataset (**S1**) must be matched against records in **S2/S3** that refer to the same real-world entity.

The challenge is not simply to find the most similar record.

The system must decide:

> **Which S2/S3 records, if any, represent the same entity as each S1 record?**

This is a difficult open-set matching problem because:

* multiple records may describe the same entity;
* fields can contain spelling variations and formatting differences;
* names can be transliterated or reordered;
* addresses can be incomplete, noisy, or differently formatted;
* country information may be inconsistent;
* some entities have multiple valid representations;
* some S1 entities have **no corresponding S2/S3 record**;
* incorrect merges are particularly expensive because the evaluation metric is precision-heavy.

Therefore, the final system must optimize **S1-level matching decisions**, not merely pairwise similarity.

---

# 2. Challenge Objective

Given:

* **S1** — query/source entities
* **S2/S3** — candidate entity corpus

the task is to produce the correct correspondence(s):

```text
S1 entity
   ↓
candidate S2/S3 entities
   ↓
entity-resolution model
   ↓
accepted match(es) / no-match
```

The challenge evaluates the resulting entity-resolution decisions using:

### Macro F₀.₅

F₀.₅ weights **precision more heavily than recall**.

This fundamentally changes the optimization strategy.

A system that produces many incorrect matches can perform worse than a conservative system that leaves uncertain cases unmatched.

Therefore:

> **False merges are one of the most important failure modes to control.**

---

# 3. What We Are Trying to Achieve

Our objective is **not merely to build a working matcher**.

The objective is to build the strongest **reproducible, compliant, precision-oriented entity-resolution pipeline** possible under the challenge constraints.

The final system should:

1. Generate a compact but high-recall candidate set.
2. Recover difficult true matches through multiple evidence sources.
3. Score candidate pairs using rich entity-level features.
4. Make decisions at the S1/entity level.
5. Explicitly handle ambiguous and singleton/open-set cases.
6. Optimize decisions for **F₀.₅**, rather than generic accuracy.
7. Minimize false merges.
8. Preserve legitimate multiple matches when supported by evidence.
9. Remain completely compliant with the challenge rules.
10. Produce a reproducible final submission.

---

# 4. Current Leaderboard Situation

The current **V1 submission** is:

```text
V1 F₀.₅ = 0.876359
```

The currently visible leading leaderboard score is:

```text
Visible leader = 0.991811
```

Current gap:

```text
0.991811 - 0.876359 = 0.115452
```

So V1 is materially below the visible leading score.

However, **V1 is frozen and immutable**.

We are not going to repeatedly modify the original submission or use leaderboard feedback as an uncontrolled optimization loop.

Instead:

> **V1 is our frozen baseline. V2 is the research program.**

The purpose of V2 is to understand *why* V1 misses valid matches and *which additional evidence/retrieval strategies can recover them without causing unacceptable false merges*.

The ultimate goal is to substantially improve F₀.₅ while maintaining precision and challenge compliance.

We should **not assume that reproducing 0.99 is guaranteed**. The engineering target is to push the system toward the strongest defensible score supported by the available evidence and evaluation process.

---

# 5. Critical Experimental Rule: V1 Is Frozen

The existing V1 submission must remain untouched.

### V1 is:

* the historical baseline;
* already submitted;
* backed up;
* the reference point for all subsequent experiments.

### V2 is:

* research only;
* isolated from the final submission process;
* used to investigate candidate generation, retrieval, features, matching and decision policies.

No V2 experiment should modify the V1 artifacts.

This gives us a clean experimental boundary:

```text
                  ┌─────────────────────┐
                  │   V1 SUBMISSION     │
                  │      FROZEN         │
                  │     0.876359        │
                  └──────────┬──────────┘
                             │
                             │ baseline
                             ▼
                  ┌─────────────────────┐
                  │   V2 RESEARCH       │
                  │                     │
                  │ candidate recovery  │
                  │ pair features       │
                  │ matcher             │
                  │ decision policy      │
                  │ validation           │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  FINAL CANDIDATE    │
                  │    SUBMISSION       │
                  └─────────────────────┘
```

---

# 6. Data and Matching Structure

The conceptual matching direction is:

```text
S1
 │
 │ query
 ▼
S2 + S3
 │
 │ candidate generation
 ▼
Candidate pairs
 │
 │ pair scoring
 ▼
Accepted / rejected matches
```

The candidate corpus consists of the permitted S2/S3 records.

The final task is therefore a large-scale entity-resolution problem rather than a simple classification problem.

---

# 7. Evaluation Philosophy

Because the challenge uses **macro F₀.₅**, optimization must happen at the appropriate level.

A naive approach would be:

```text
pair similarity
      ↓
pair threshold
      ↓
prediction
```

That is insufficient.

The correct conceptual pipeline is:

```text
candidate generation
        ↓
pair-level evidence
        ↓
pair scoring
        ↓
S1-level aggregation
        ↓
ambiguity / singleton handling
        ↓
decision policy
        ↓
final matching results
```

The model should therefore be evaluated on:

* precision;
* recall;
* F₀.₅;
* false merges;
* unmatched/singleton behavior;
* per-S1 decision quality;
* candidate recall;
* confidence/calibration;
* ambiguity handling.

---

# 8. Current State — Candidate Generation

The candidate-generation / blocking stage is currently **frozen and reproduced**.

Current blocking configuration:

```text
50 / 50 + heavy100
```

The validation candidate set contains:

```text
34,568,979 candidate pairs
```

Known ground-truth links represented:

```text
670,785 ground-truth links
```

Candidate-generation recall:

```text
87.8084%
```

Reproduction status:

```text
86 / 86 tests passed
```

Peak observed RSS:

```text
648.5 MiB
```

This is an important milestone because the candidate generator is now deterministic and reproducible.

---

# 9. What the Blocking Result Means

The current blocker is strong enough to provide a substantial search-space reduction, but it is **not the final matcher**.

Approximately:

```text
34.57M
```

candidate pairs are passed forward instead of comparing every S1 entity against every S2/S3 record.

At the same time, candidate recall is approximately:

```text
87.81%
```

Therefore, some true matches are currently being lost **before the matcher even gets a chance to score them**.

This creates two distinct error sources:

### Error Source A — Retrieval Failure

The true S2/S3 entity never enters the candidate set.

```text
True match
   ↓
blocking
   ↓
NOT retrieved
   ↓
matcher cannot recover it
```

### Error Source B — Matching Failure

The true entity is retrieved, but the matcher rejects it or chooses another entity.

```text
True match
   ↓
blocking
   ↓
candidate retrieved
   ↓
matcher
   ↓
incorrect decision
```

These two failure types must be analyzed separately.

---

# 10. Current Research Direction

The next stage is **not simply "train a model."**

The immediate research objective is:

> Determine how much of the remaining performance gap comes from candidate-retrieval failure versus pair-scoring/decision failure.

The research sequence is:

```text
Historical-touch firewall
        ↓
Candidate / research split
        ↓
Frozen V1 baseline
        ↓
Miss decomposition
        ↓
Evidence-gated retrieval passes
        ↓
Materialized Pareto frontier
        ↓
STOP / select final research configuration
        ↓
Train/finalize matcher
        ↓
S1-level decision optimization
        ↓
Validation
        ↓
Final submission
```

---

# 11. Historical-Touch Firewall

A major requirement of the current research protocol is to prevent accidental contamination from previously used labelled information.

The repository contains historical `model_fit` S1s that participated in an earlier labelled audit.

A specific audit found:

```text
Historical model_fit S1s: 1,655,792
Eligible pool after the required exclusion: 0
```

Therefore:

> Those historical S1s must not be reused as fresh eligible labelled research data.

This is a hard experimental constraint.

The research process must work from the currently permitted corpus rather than silently reusing old labelled observations.

---

# 12. Why This Matters

Entity resolution is especially vulnerable to leakage.

A seemingly harmless action such as:

```text
use previous labels
        ↓
discover a matching pattern
        ↓
tune a blocking rule
        ↓
evaluate on the same population
```

can produce an unrealistically strong result.

The project therefore prioritizes:

* provenance;
* dataset eligibility;
* experiment isolation;
* frozen artifacts;
* deterministic reproduction;
* auditability.

A higher score obtained through contaminated evaluation is not considered a valid improvement.

---

# 13. Candidate Research Strategy

The research stage should explore **additional candidate-generation mechanisms** rather than immediately increasing the size of the existing candidate set indiscriminately.

The desired property is:

> Add candidates that are likely to contain missed true matches while adding as few unnecessary pairs as possible.

Potential evidence families include:

### Name evidence

* exact normalized name;
* token overlap;
* token-set similarity;
* character similarity;
* edit distance;
* transliteration/variant handling;
* token order robustness.

### Address evidence

* normalized address;
* token overlap;
* locality/city/state information;
* postal-code evidence where available;
* partial address correspondence;
* noisy formatting tolerance.

### Country evidence

* exact country;
* normalized country;
* country compatibility;
* missing-country handling.

### Cross-field evidence

Examples:

```text
name + country
name + address
name + postal code
address + country
name + address + country
```

The objective is not to create hundreds of arbitrary blocks.

It is to discover **evidence-gated retrieval passes** with measurable incremental value.

---

# 14. Candidate Research Must Be Measured

Every additional retrieval pass must answer:

1. How many new candidates did it add?
2. How many previously missed true matches did it recover?
3. What is the incremental recall?
4. How many total pairs did it introduce?
5. What is the computational/memory cost?
6. Does it duplicate existing candidates?
7. Does it create a useful precision/recall trade-off downstream?

The research output should therefore be treated as a measurable Pareto problem.

Conceptually:

```text
             Candidate recall
                  ↑
                  │        ●
                  │     ●
                  │   ●
                  │ ●
                  └────────────────→ Candidate volume / cost
```

We want configurations that provide meaningful recall improvements without exploding the candidate set.

---

# 15. Why Deduplication Happens After Candidate Generation

Deduplication should not prematurely destroy the evidence produced by separate retrieval paths.

Multiple blocking/retrieval strategies may independently discover the same pair:

```text
Pass A ─────┐
Pass B ─────┼──→ same S1/S2 pair
Pass C ─────┘
```

The system should first materialize the candidate evidence and then deduplicate the final pair identity.

Conceptually:

```text
retrieval pass A
retrieval pass B
retrieval pass C
retrieval pass D
        ↓
union/materialization
        ↓
deduplicate pair identity
        ↓
final candidate_pairs
```

This preserves the ability to analyze:

* which retrieval pass found the pair;
* whether multiple independent signals retrieved it;
* incremental recall;
* retrieval provenance.

Premature deduplication can make such analysis unnecessarily difficult.

---

# 16. Pair-Level Feature Engineering

Once candidate generation is sufficiently strong, each candidate pair can be represented using rich pairwise features.

The feature families should include:

### Name

* normalized exact equality;
* character similarity;
* token similarity;
* Jaccard similarity;
* edit-distance-derived features;
* token containment;
* token count;
* shared-token statistics.

### Address

* normalized exact/partial equality;
* token overlap;
* Jaccard similarity;
* character similarity;
* shared locality information;
* postal-code agreement where available.

### Country

* exact agreement;
* normalized agreement;
* missingness;
* compatibility indicators.

### Cross-field consistency

Examples:

```text
name similarity × address similarity
name similarity × country agreement
address similarity × country agreement
```

### Missingness

Missing data must be represented explicitly.

A missing field should not automatically become:

```text
similarity = 0
```

because missingness and disagreement are different states.

---

# 17. Matcher

After candidate generation and feature engineering, the next stage is a supervised or otherwise evidence-based pair matcher.

The matcher should estimate:

```text
P(pair is a true entity match | pair features)
```

or an equivalent ranking/confidence score.

The important point is:

> The matcher is not the whole system.

A strong matcher cannot recover a true match that candidate generation never retrieved.

Therefore:

```text
Final quality
≈
candidate recall
×
pair discrimination
×
decision policy quality
```

---

# 18. S1-Level Decision Layer

Pair scores alone are insufficient.

For each S1 entity, the system should consider the complete candidate set:

```text
S1_i
 ├── candidate A → 0.97
 ├── candidate B → 0.95
 ├── candidate C → 0.41
 └── candidate D → 0.18
```

The final decision may depend on:

* absolute score;
* score margin;
* number of strong alternatives;
* evidence consistency;
* candidate rank;
* singleton/open-set probability;
* field completeness;
* duplicate/multiple-match behavior.

Therefore, the decision layer should operate at the **S1 level**, not independently on every pair.

---

# 19. Singleton / Open-Set Handling

One of the most important requirements is handling S1 entities that do not have a valid S2/S3 counterpart.

The system must therefore support:

```text
S1 → valid match
```

and

```text
S1 → no valid match
```

rather than forcing every S1 entity to match something.

A weak system often does:

```text
choose highest-scoring candidate
```

even when all candidates are poor.

That can generate large numbers of false positives.

The final decision policy must therefore explicitly model the possibility of:

> **No match / singleton / open-set entity.**

---

# 20. Precision Strategy

Because F₀.₅ is precision-heavy, the system must be particularly conservative around ambiguous cases.

Examples of dangerous situations:

```text
Candidate A = 0.89
Candidate B = 0.88
```

versus:

```text
Candidate A = 0.99
Candidate B = 0.31
```

The absolute score alone may not tell the complete story.

The second case provides substantially stronger separation.

Therefore, final decisions should consider:

* score;
* margin;
* evidence consistency;
* competing candidates;
* field reliability;
* singleton probability.

---

# 21. What Has Already Been Completed

### V1 baseline

* V1 submission created.
* V1 score recorded.
* V1 artifacts backed up.
* V1 declared immutable.

### Eligibility audit

* Historical labelled usage investigated.
* Historical `model_fit` population identified.
* Required exclusion applied.
* Eligible pool from that historical population confirmed as zero.

### Blocking

* Current blocking configuration frozen.
* `50/50 + heavy100` reproduced.
* 34,568,979 validation candidates generated.
* 670,785 ground-truth links represented.
* 87.8084% candidate recall measured.
* Peak RSS measured at 648.5 MiB.
* 86/86 tests passed.

### Current conclusion

The blocking stage is now sufficiently reproducible to serve as a reliable experimental baseline.

---

# 22. What Has NOT Been Finalized Yet

The following are **not yet final**:

* final pair features;
* final matcher;
* matcher hyperparameters;
* pair threshold;
* S1-level decision threshold;
* margin policy;
* singleton/open-set policy;
* final candidate-generation union;
* final `candidate_pairs.tsv`;
* final `matching_results.tsv`;
* final submission;
* final leaderboard score.

This distinction is important.

We should not claim that the current pipeline is the final solution.

---

# 23. Immediate Next Milestones

## Milestone 1 — Complete Candidate Miss Decomposition

Determine:

```text
Which true matches are missed by blocking?
Which true matches are retrieved but later difficult to distinguish?
```

This establishes the actual bottleneck.

---

## Milestone 2 — Research Additional Retrieval Passes

Develop evidence-based retrieval passes targeting the known missed-match patterns.

Measure each pass independently.

Required outputs:

```text
candidate_count
new_candidate_count
new_true_links
incremental_recall
runtime
memory
provenance
```

---

## Milestone 3 — Materialize the Pareto Frontier

Compare retrieval configurations based on:

```text
candidate volume
vs.
candidate recall
vs.
computational cost
```

Do not blindly choose the configuration with the largest candidate set.

Select the configuration that provides the strongest useful recall/cost trade-off.

---

## Milestone 4 — Freeze Final Candidate Generation

Once research shows that additional retrieval passes no longer provide sufficient incremental value:

```text
FINAL BLOCKING / RETRIEVAL
```

is frozen.

No continuous uncontrolled experimentation should follow.

---

## Milestone 5 — Build Pair Features

Generate the final feature matrix from the frozen candidate set.

---

## Milestone 6 — Train Matcher

Train the pair-level matcher using only permitted training information.

---

## Milestone 7 — Optimize S1-Level Decisions

Tune:

* match threshold;
* score margin;
* ambiguity handling;
* singleton rejection;
* multiple-match policy.

Optimize specifically for:

```text
Macro F₀.₅
```

rather than generic pair accuracy.

---

## Milestone 8 — Validation

Perform a complete validation audit covering:

```text
precision
recall
F₀.₅
false merges
singleton behavior
per-S1 decisions
candidate recall
```

---

## Milestone 9 — Generate Final Artifacts

Expected final artifacts include:

```text
candidate_pairs.tsv
matching_results.tsv
```

plus the required supporting metadata/configuration.

---

## Milestone 10 — Reproducibility & Submission Audit

Before submission:

* rerun the pipeline;
* verify deterministic outputs;
* verify no forbidden data was touched;
* verify train/test boundaries;
* verify artifact schemas;
* verify row counts;
* verify output formatting;
* verify no accidental V1 mutation;
* preserve experiment metadata;
* package the final submission.

---

# 24. Target Architecture

The intended final architecture is:

```text
                    ┌─────────────────────┐
                    │        S1           │
                    └──────────┬──────────┘
                               │
                               ▼
                  ┌────────────────────────┐
                  │ Candidate Generation   │
                  │                        │
                  │ 50/50 + heavy100       │
                  │ + researched passes    │
                  └────────────┬───────────┘
                               │
                               ▼
                  ┌────────────────────────┐
                  │ Candidate Union        │
                  │ + Provenance           │
                  │ + Deduplication        │
                  └────────────┬───────────┘
                               │
                               ▼
                  ┌────────────────────────┐
                  │ Pair Feature Engine    │
                  │                        │
                  │ Name                   │
                  │ Address                │
                  │ Country                │
                  │ Cross-field evidence   │
                  └────────────┬───────────┘
                               │
                               ▼
                  ┌────────────────────────┐
                  │ Pair Matcher           │
                  │                        │
                  │ Match probability      │
                  │ / ranking score        │
                  └────────────┬───────────┘
                               │
                               ▼
                  ┌────────────────────────┐
                  │ S1-Level Decision      │
                  │                        │
                  │ Threshold              │
                  │ Margin                 │
                  │ Ambiguity              │
                  │ Singleton              │
                  └────────────┬───────────┘
                               │
                               ▼
                  ┌────────────────────────┐
                  │ Final Matching Results │
                  └────────────────────────┘
```

---

# 25. Core Design Principles

## Principle 1 — Recall First at Retrieval

If the true entity never enters the candidate set, the matcher has zero chance to recover it.

Therefore candidate generation must have high recall.

---

## Principle 2 — Precision First at Final Decision

A candidate being retrieved does not mean it should be accepted.

Final matching decisions must be conservative because F₀.₅ heavily rewards precision.

---

## Principle 3 — Never Force a Match

The correct answer can be:

```text
NO MATCH
```

---

## Principle 4 — Separate Retrieval From Matching

Do not hide retrieval failures inside matcher metrics.

Always distinguish:

```text
retrieval failure
```

from:

```text
classification/decision failure
```

---

## Principle 5 — Optimize the Actual Metric

The optimization target is:

```text
Macro F₀.₅
```

not:

```text
accuracy
F1
pair recall alone
ROC-AUC
```

Those may be diagnostic metrics, but they are not the final objective.

---

## Principle 6 — Protect Against Leakage

No experiment should use information that is unavailable under the challenge rules.

Historical labelled data must not silently become training/evaluation data.

---

## Principle 7 — Every Improvement Must Be Measurable

A new technique is useful only if we can demonstrate its incremental contribution.

No:

```text
"this should improve recall"
```

without measurement.

Instead:

```text
baseline recall
→ new recall
→ incremental links recovered
→ candidate cost
→ downstream F₀.₅ impact
```

---

## Principle 8 — Preserve Reproducibility

Every meaningful experiment should record:

* input population;
* retrieval configuration;
* candidate counts;
* recovered links;
* feature version;
* model version;
* threshold;
* evaluation results;
* resource usage.

---

# 26. Research Stop Rule

The project should not continue adding retrieval passes indefinitely.

Research should stop when the materialized evidence shows that additional candidate-generation complexity is no longer producing sufficiently valuable incremental recovery.

The objective is not:

> Maximum number of candidates.

The objective is:

> **Maximum useful candidate recall with controlled computational cost and downstream precision.**

Once the Pareto frontier stabilizes, freeze the retrieval layer and move forward.

---

# 27. Final Optimization Objective

The final optimization can be thought of as:

```text
MAXIMIZE

    Macro F₀.₅

SUBJECT TO

    high candidate recall
    controlled candidate volume
    high precision
    low false-merge rate
    correct singleton handling
    reproducible evaluation
    challenge compliance
```

The system should therefore balance three layers:

```text
┌──────────────────────────────┐
│ 1. RETRIEVAL                 │
│    Don't miss true matches   │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ 2. MATCHING                  │
│    Separate true/false pairs │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ 3. DECISION                  │
│    Accept / reject / unknown │
└──────────────────────────────┘
```

---

# 28. Definition of Success

The project is successful when we have a final pipeline that:

### Data integrity

* uses only permitted information;
* has no hidden historical-label leakage;
* has a documented provenance chain.

### Retrieval

* materially improves on the current 87.8084% candidate recall where recoverable;
* maintains manageable candidate volume;
* has reproducible retrieval behavior.

### Matching

* effectively separates true and false entity pairs;
* handles noisy names, addresses and countries;
* exploits cross-field evidence.

### Decision making

* optimizes S1-level F₀.₅;
* avoids forced matches;
* handles singleton/open-set entities;
* minimizes false merges.

### Engineering

* runs reproducibly;
* passes validation;
* produces correctly formatted artifacts;
* remains computationally practical.

### Competition

The final objective is to move substantially beyond:

```text
V1 = 0.876359
```

and close as much of the gap as the evidence-supported system can achieve toward the current visible benchmark:

```text
0.991811
```

without compromising validity, reproducibility, or challenge compliance.

---

# 29. Current Project Status

```text
╔══════════════════════════════════════════════════════╗
║              ENTITY RESOLUTION STATUS                ║
╠══════════════════════════════════════════════════════╣
║ V1 baseline                  COMPLETE / FROZEN        ║
║ V1 score                    0.876359                  ║
║ Visible leaderboard leader  0.991811                  ║
║ Historical-label audit      COMPLETE                  ║
║ Eligible historical pool    0                         ║
║ Blocking                     COMPLETE / FROZEN        ║
║ Candidate pairs              34,568,979               ║
║ GT links represented         670,785                  ║
║ Candidate recall             87.8084%                 ║
║ Blocking tests               86/86 PASS               ║
║ Peak RSS                     648.5 MiB                ║
║ Pair features                NOT FINAL                ║
║ Matcher                      NOT FINAL                ║
║ Decision policy              NOT FINAL                ║
║ Final candidates             NOT GENERATED            ║
║ Final predictions            NOT GENERATED             ║
║ Final submission             NOT READY                ║
╚══════════════════════════════════════════════════════╝
```

---

# 30. Immediate Next Action

The immediate priority is **not submission generation**.

The immediate priority is:

```text
MISS DECOMPOSITION
        ↓
EVIDENCE-GATED RETRIEVAL RESEARCH
        ↓
PARETO FRONTIER
        ↓
FREEZE FINAL CANDIDATE GENERATION
```

Only after this stage should the project proceed aggressively into:

```text
features
→ matcher
→ S1-level decision policy
→ validation
→ final artifacts
→ submission
```

---

# 31. One-Sentence Project Goal

> **Build a leakage-free, reproducible, precision-oriented entity-resolution system that maximizes Macro F₀.₅ by combining high-recall candidate retrieval, strong pairwise evidence, and S1-level open-set decision making, with the explicit goal of substantially improving the frozen V1 score of 0.876359 toward the current 0.991811 leaderboard benchmark.**

---

# 32. Golden Rule for Future Work

Before implementing any new idea, answer:

```text
1. What failure mode does this address?
2. Is it retrieval, matching, or decision making?
3. Is the data legally/experimentally eligible?
4. How will we measure its incremental benefit?
5. What is the computational cost?
6. Could it increase false merges?
7. Does it improve the actual F₀.₅ objective?
8. Can we reproduce the result?
```

If these questions cannot be answered, the change should not become part of the final pipeline.

---

## Final Mental Model

This project is **not**:

```text
"Train a classifier and submit predictions."
```

It is:

```text
                 ENTITY RESOLUTION SYSTEM
                          │
          ┌───────────────┼────────────────┐
          ▼               ▼                ▼
     RETRIEVAL         MATCHING        DECISION
     ---------         --------        --------
     Find possible     Measure         Decide whether
     true entities     pair evidence   to actually
                                       accept a match
          │               │                │
          └───────────────┼────────────────┘
                          ▼
                   Macro F₀.₅
                          │
                          ▼
                  FINAL SUBMISSION
```

The current state is therefore:

> **V1 is frozen. Blocking is frozen and validated. We are currently researching how to recover the ~12.19% of ground-truth links not represented by the current candidate generator, while preserving precision and preventing leakage. Once retrieval research reaches a defensible Pareto frontier, we freeze candidates, build the matcher and S1-level decision layer, validate the complete system, and only then produce the final submission.**
