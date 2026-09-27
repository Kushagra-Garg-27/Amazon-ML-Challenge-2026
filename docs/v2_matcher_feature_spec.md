# V2 matcher feature definitions

The exact 100-column order is frozen in `work/v2_matcher_sprint_r1/feature_spec.json`.
The original 61 columns retain the definitions in `src/er/features/{exact,token,fuzzy}.py`
and `src/er/test_pipeline/features.py`. Their values come from the frozen Phase 2
feature partitions; the sprint does not recompute or alter them.

All additional columns are float32 and deterministic. Inputs are normalized
observable S1/target attributes, label-free target token frequencies, frozen V1
scores and fixed candidate sets. Labels, S1/target IDs and internal split roles
are excluded from the model feature order. IDs only join observable records.

## Names

Both strings must be nonempty; otherwise all name similarities/compatibility
flags are zero. Character n-grams include spaces. Jaccard is intersection size
divided by union size, with an empty union returning zero.

| Column | Definition |
| --- | --- |
| name_wratio | RapidFuzz WRatio / 100 |
| name_ng2, name_ng3, name_ng4 | Set Jaccard of character 2-, 3-, and 4-grams |
| name_levenshtein | RapidFuzz normalized Levenshtein similarity |
| name_jaro | RapidFuzz normalized Jaro-Winkler similarity |
| name_compact_ratio | RapidFuzz ratio / 100 after removing spaces |
| initial_compat | Equal token initials, or one space-free name equals the other's initials |
| prefix_compat, suffix_compat | One nonempty space-free name starts/ends with the other |
| name_weighted_jaccard | Sum of weights on common tokens / sum on union |

Token weights are `log(1 + 10320219 / country_target_DF)` from frozen, observable
target records. Unknown tokens weigh one. Empty unions score zero. The numerator
uses a fixed total-target constant, not any outcome-derived statistic.
QRatio duplicates the existing ratio for nonempty normalized input, so it is not
added. Existing token sort/set, partial ratio, containment and length features
remain available. Correlation reduction uses internal training rows only.

## Script

`translit_ratio`, `translit_token_ratio` and `translit_ng3` compare locally
romanized names by ratio/100, token-sort ratio/100 and character trigram Jaccard.
Either empty romanized string yields zero. The frozen implementation uses NFKD,
a Devanagari consonant/vowel/mark table, nasal/visarga mappings, and final-schwa
removal for tokens longer than three characters. It leaves unsupported scripts
unchanged. This is a limited heuristic: lookahead uses original-string positions
while iteration uses NFKD, so decomposed characters may romanize imperfectly.
Mixed Latin/Devanagari final-schwa behavior is also approximate. These limits are
preserved in the versioned feature artifact; no translation service is used.

## Address

Numeric strings are regex `\d+`; locality tokens contain no digit. No external
city/state dictionary is used, and locality is a token proxy, not a place parser.

| Column | Definition and missing behavior |
| --- | --- |
| addr_missing | Either normalized address empty |
| addr_weak | Both present and the shorter nonnumeric-token count is below two |
| addr_ng3 | Address trigram Jaccard; zero if either absent |
| addr_levenshtein | Normalized Levenshtein similarity; zero if either absent |
| addr_locality_jaccard | Jaccard of nonnumeric tokens; empty union zero |
| number_jaccard | Jaccard of distinct numeric strings; empty union zero |
| house_conflict_v2 | Both have numeric strings and their first strings differ; missing is not conflict |
| postal_equal_v2 | At least one common five- or six-digit string; absent zero |
| phone_equal_v2 | At least one common numeric string of length nine or more; absent zero |
| addr_weighted_jaccard | Address token weighted Jaccard using address DF; empty union zero |

First-number, postal and phone comparisons are evidence proxies, not validated
semantic extraction. Country agreement, house-number equality, numeric conflict,
containment and shared rare-token evidence are already in V1.

## Opposite-source evidence

For each S1, keep at most two frozen-score >=0.61 target anchors per source,
ordered by score descending then target ID. A candidate only compares against
the opposite source and never against itself. No truth assignment enters anchor
selection. With no usable anchors all seven values are zero.

| Column | Definition over eligible anchors |
| --- | --- |
| cross_name | Maximum nonempty name ratio / 100 |
| cross_address | Maximum nonempty address token-set ratio / 100 |
| cross_translit | Maximum local romanized name ratio / 100 |
| cross_numeric | Maximum address numeric-set Jaccard |
| cross_joint | Maximum `min(name_ratio,address_ratio) * anchor_score` from the same anchor |
| cross_support_count | Count with name >=0.8 and (address >=0.6 or transliteration >=0.9) |
| cross_seed_score | Maximum anchor's frozen V1 score |

Individual maxima may come from different anchors; the joint feature enforces
same-anchor support. Degree is bounded by two, avoiding all-pairs comparisons.

## Entity score context

| Column | Definition |
| --- | --- |
| v1_score | Candidate's frozen V1 prediction |
| candidate_count | Total fixed candidates for this S1 across both sources |
| source_candidate_count | Fixed candidates for this S1 and candidate source |
| top_score | Maximum frozen score in that source neighborhood |
| second_score | Second-highest score, or zero if absent |
| score_gap | Top minus second, with absent second treated as zero |
| high_count | Source-neighborhood scores >=0.61 |
| relative_to_top | Candidate frozen score minus source-neighborhood top score |

The context uses every candidate before negative sampling. All scored candidates
have a source neighborhood, so counts and top score are always defined. No
validation outcome contributes to any score summary. All truth-linked diagnostics
and hard-negative mining are confined to the internal training role.

## Residual decision context

The optional residual decision model adds nine float32 values computed from the
already frozen V2 model: candidate score; S1 top score; second score (zero if no
second); top-minus-second gap; candidate-minus-top; sum of S1 scores; count >=0.7;
candidate-source top score; candidate-minus-source-top. Candidate sets are complete
before computing these aggregates. Ties share the same values. They use no labels.
The residual learner fits only model-selection S1s, using the base clipped logit
as its initial prediction. Clipping to [1e-6,1-1e-6] makes endpoints finite.
Inference adds the saved residual raw prediction to this base logit and applies
the sigmoid. A synthetic regression checks the saved model against LightGBM's
training-time predictions. Residual loss/negative mining uses only its declared
fit role; it does not change the primary model's training role.
