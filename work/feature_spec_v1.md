# Feature specification v1

Version: `feature_spec_v1`. 65 deterministic, label-free numeric features.

Country is handled as open-set agreement only. Labels are stored separately. Candidate ranks are copied from frozen inference-time ranking artifacts and are never recomputed from labels.

| Feature | Type | Group | Default | Computation |
|---|---|---|---:|---|
| `provenance` | uint8 | provenance | `0` | Frozen candidate provenance bitmask. |
| `retrieved_sorted_name` | bool | provenance | `False` | provenance & 1 != 0 |
| `retrieved_exact_address` | bool | provenance | `False` | provenance & 2 != 0 |
| `retrieved_name_token` | bool | provenance | `False` | provenance & 4 != 0 |
| `retrieved_address_token` | bool | provenance | `False` | provenance & 8 != 0 |
| `retrieval_pass_count` | uint8 | provenance | `0` | Population count of bits 1,2,4,8. |
| `name_token_rank` | uint16 | provenance | `0` | Frozen per-source name-token rank; zero when absent. |
| `address_token_rank` | uint16 | provenance | `0` | Frozen per-source address-token rank; zero when absent. |
| `source_balanced_rank` | uint16 | provenance | `0` | Minimum non-zero frozen token rank. |
| `heavy_sorted_block` | bool | provenance | `False` | Exact sorted-name target block has at least 120 rows. |
| `target_is_s2` | bool | provenance | `False` | Target namespace is S2. |
| `target_is_s3` | bool | provenance | `False` | Target namespace is S3. |
| `exact_name_norm` | bool | exact | `False` | Equal non-empty normalized name. |
| `exact_name_sorted` | bool | exact | `False` | Equal non-empty sorted-token name. |
| `exact_address_norm` | bool | exact | `False` | Equal non-empty normalized address. |
| `exact_name_nosuffix` | bool | exact | `False` | Equal non-empty suffix-stripped name. |
| `exact_numeric_set` | bool | exact | `False` | Equal non-empty address numeric-token sets. |
| `exact_postal_token` | bool | exact | `False` | Equal non-empty first 5-6 digit token. |
| `country_agreement` | bool | exact | `False` | Equal non-empty open-set normalized country. |
| `s1_name_missing` | bool | missing_length | `True` | S1 normalized name empty. |
| `target_name_missing` | bool | missing_length | `True` | Target normalized name empty. |
| `s1_address_missing` | bool | missing_length | `True` | S1 normalized address empty. |
| `target_address_missing` | bool | missing_length | `True` | Target normalized address empty. |
| `s1_name_chars` | uint16 | missing_length | `0` | Normalized name character count. |
| `target_name_chars` | uint16 | missing_length | `0` | Normalized name character count. |
| `s1_address_chars` | uint16 | missing_length | `0` | Normalized address character count. |
| `target_address_chars` | uint16 | missing_length | `0` | Normalized address character count. |
| `s1_name_tokens` | uint16 | missing_length | `0` | Distinct suffix-stripped name tokens. |
| `target_name_tokens` | uint16 | missing_length | `0` | Distinct suffix-stripped name tokens. |
| `s1_address_tokens` | uint16 | missing_length | `0` | Distinct address tokens. |
| `target_address_tokens` | uint16 | missing_length | `0` | Distinct address tokens. |
| `name_char_abs_diff` | uint16 | missing_length | `0` | Absolute normalized-name length difference. |
| `address_char_abs_diff` | uint16 | missing_length | `0` | Absolute normalized-address length difference. |
| `name_char_relative_diff` | float32 | missing_length | `0.0` | Absolute difference divided by maximum length. |
| `address_char_relative_diff` | float32 | missing_length | `0.0` | Absolute difference divided by maximum length. |
| `name_token_intersection` | uint16 | token | `0` | Distinct name-token intersection size. |
| `name_token_union` | uint16 | token | `0` | Distinct name-token union size. |
| `name_jaccard` | float32 | token | `0.0` | Name intersection / union. |
| `name_containment_s1` | float32 | token | `0.0` | Name intersection / S1 token count. |
| `name_containment_target` | float32 | token | `0.0` | Name intersection / target token count. |
| `address_token_intersection` | uint16 | token | `0` | Distinct address-token intersection size. |
| `address_token_union` | uint16 | token | `0` | Distinct address-token union size. |
| `address_jaccard` | float32 | token | `0.0` | Address intersection / union. |
| `address_containment_s1` | float32 | token | `0.0` | Address intersection / S1 token count. |
| `address_containment_target` | float32 | token | `0.0` | Address intersection / target token count. |
| `name_shared_idf` | float32 | token | `0.0` | Sum 1/target-DF for shared eligible name tokens. |
| `address_shared_idf` | float32 | token | `0.0` | Sum 1/target-DF for shared eligible address tokens. |
| `shared_address_numbers` | uint16 | numeric | `0` | Count of shared distinct digit sequences. |
| `conflicting_address_numbers` | bool | numeric | `False` | Both have numbers and their sets are disjoint. |
| `shared_postal_tokens` | uint8 | numeric | `0` | Equal non-empty first 5-6 digit token. |
| `conflicting_postal_tokens` | bool | numeric | `False` | Both have non-empty postal tokens that differ. |
| `digit_sequence_equal` | bool | numeric | `False` | Ordered digit-sequence lists are equal and non-empty. |
| `house_number_equal` | bool | numeric | `False` | First address number is equal and non-empty. |
| `name_ratio` | float32 | fuzzy | `0.0` | RapidFuzz ratio / 100. |
| `name_partial_ratio` | float32 | fuzzy | `0.0` | RapidFuzz partial_ratio / 100. |
| `name_token_sort_ratio` | float32 | fuzzy | `0.0` | RapidFuzz token_sort_ratio / 100. |
| `name_token_set_ratio` | float32 | fuzzy | `0.0` | RapidFuzz token_set_ratio / 100. |
| `address_ratio` | float32 | fuzzy | `0.0` | RapidFuzz ratio / 100; zero if either address empty. |
| `address_partial_ratio` | float32 | fuzzy | `0.0` | RapidFuzz partial_ratio / 100; zero if missing. |
| `address_token_sort_ratio` | float32 | fuzzy | `0.0` | RapidFuzz token_sort_ratio / 100; zero if missing. |
| `address_token_set_ratio` | float32 | fuzzy | `0.0` | RapidFuzz token_set_ratio / 100; zero if missing. |
| `s1_script_class` | uint8 | script | `0` | 0 empty, 1 Latin, 2 Devanagari, 3 mixed, 4 other. |
| `target_script_class` | uint8 | script | `0` | 0 empty, 1 Latin, 2 Devanagari, 3 mixed, 4 other. |
| `same_script_class` | bool | script | `False` | Equal non-zero script class. |
| `script_conflict` | bool | script | `False` | Different non-zero script classes. |
