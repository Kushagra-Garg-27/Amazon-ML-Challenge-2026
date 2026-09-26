# Feature v1 audit

Audited 65 features on a deterministic 100,000-row correlation sample and the full bounded pilot population.

Original spec: `51f1a33eab22780fee39e29f69117faf7c8c6eb47658499c6f53f564c5245582`. Corrected v1.1 spec: `37982fd6377aa2e36fe1c5dec0484a35c02ba4bcc4e3b67b3a337b33597f6e1f`.

v1.1 corrects `conflicting_address_numbers` prose and omits four redundant model inputs. The physical v1 artifacts, numeric values, and 65 stored columns remain unchanged; the v1.1 training matrix has 61 ordered inputs.

Exact duplicate pairs on the sample: `[['retrieved_sorted_name', 'exact_name_sorted'], ['retrieved_exact_address', 'exact_address_norm'], ['exact_postal_token', 'shared_postal_tokens']]`.
Perfect-correlation pairs on the sample: `[['target_is_s2', 'target_is_s3', -1.0]]`.
Constant features: `['country_agreement', 's1_name_missing', 'target_name_missing', 's1_address_missing', 's1_script_class']`. Near-constant features: `['country_agreement', 's1_name_missing', 'target_name_missing', 's1_address_missing', 's1_script_class']`.

No label leakage or test-time unavailable input was found. Country is represented only by open-set equality.

| Feature | Type | Min | Median | Max | Std | Unique~ | Positive mean | Negative mean |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| provenance | uint8 | 1 | 8 | 15 | 2.61209 | 17 | 8.84492 | 5.84676 |
| retrieved_sorted_name | bool | 0 | 0 | 1 | 0.343172 | 2 | 0.576622 | 0.12764 |
| retrieved_exact_address | bool | 0 | 0 | 1 | 0.047065 | 2 | 0.0928043 | 0.000425561 |
| retrieved_name_token | bool | 0 | 0 | 1 | 0.475598 | 2 | 0.514168 | 0.34237 |
| retrieved_address_token | bool | 0 | 1 | 1 | 0.497722 | 2 | 0.753252 | 0.543599 |
| retrieval_pass_count | uint8 | 1 | 1 | 4 | 0.207723 | 4 | 1.93685 | 1.01403 |
| name_token_rank | uint16 | 0 | 0 | 50 | 14.4721 | 45 | 1.17411 | 8.65645 |
| address_token_rank | uint16 | 0 | 4.63391 | 50 | 16.4645 | 45 | 1.96293 | 14.0086 |
| source_balanced_rank | uint16 | 0 | 21.7391 | 50 | 15.7129 | 45 | 2.54003 | 22.6556 |
| heavy_sorted_block | bool | 0 | 0 | 1 | 0.292115 | 2 | 0.0711032 | 0.0946633 |
| target_is_s2 | bool | 0 | 0.769594 | 1 | 0.499974 | 2 | 0.486095 | 0.505475 |
| target_is_s3 | bool | 0 | 0.0773356 | 1 | 0.499974 | 2 | 0.513905 | 0.494525 |
| exact_name_norm | bool | 0 | 0 | 1 | 0.191464 | 2 | 0.293479 | 0.0330519 |
| exact_name_sorted | bool | 0 | 0 | 1 | 0.343172 | 2 | 0.576622 | 0.12764 |
| exact_address_norm | bool | 0 | 0 | 1 | 0.047065 | 2 | 0.0928043 | 0.000425561 |
| exact_name_nosuffix | bool | 0 | 0 | 1 | 0.338805 | 2 | 0.536614 | 0.124279 |
| exact_numeric_set | bool | 0 | 0 | 1 | 0.333912 | 2 | 0.637323 | 0.117747 |
| exact_postal_token | bool | 0 | 0 | 1 | 0.132933 | 2 | 0.0555994 | 0.0172502 |
| country_agreement | bool | 1 | 1 | 1 | 0 | 1 | 1 | 1 |
| s1_name_missing | bool | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| target_name_missing | bool | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| s1_address_missing | bool | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| target_address_missing | bool | 0 | 0 | 1 | 0.135947 | 2 | 0.0371392 | 0.0184739 |
| s1_name_chars | uint16 | 3 | 23 | 66 | 7.64674 | 52 | 23.8909 | 23.565 |
| target_name_chars | uint16 | 1 | 22 | 123 | 8.81856 | 91 | 23.4947 | 23.0672 |
| s1_address_chars | uint16 | 14 | 37.8364 | 187 | 23.6085 | 138 | 48.4794 | 48.32 |
| target_address_chars | uint16 | 0 | 35.375 | 217 | 20.6035 | 223 | 43.6412 | 42.1444 |
| s1_name_tokens | uint16 | 1 | 2 | 9 | 0.940269 | 10 | 2.6472 | 2.63238 |
| target_name_tokens | uint16 | 1 | 2 | 18 | 1.05401 | 20 | 2.78209 | 2.66085 |
| s1_address_tokens | uint16 | 3 | 7 | 26 | 3.60332 | 24 | 8.09534 | 8.09089 |
| target_address_tokens | uint16 | 0 | 6 | 35 | 3.41355 | 36 | 7.47841 | 7.14691 |
| name_char_abs_diff | uint16 | 0 | 6.48274 | 91 | 6.705 | 66 | 3.47079 | 8.08954 |
| address_char_abs_diff | uint16 | 0 | 8.63621 | 187 | 15.6263 | 163 | 8.91751 | 14.1422 |
| name_char_relative_diff | float32 | 0 | 0.252665 | 0.976744 | 0.194666 | 1621 | 0.12828 | 0.279026 |
| address_char_relative_diff | float32 | 0 | 0.197251 | 1 | 0.194037 | 6848 | 0.166543 | 0.23996 |
| name_token_intersection | uint16 | 0 | 0 | 9 | 0.852422 | 11 | 2.14737 | 0.639584 |
| name_token_union | uint16 | 1 | 4.03083 | 20 | 1.81185 | 21 | 3.28192 | 4.65364 |
| name_jaccard | float32 | 0 | 0 | 1 | 0.336768 | 55 | 0.738936 | 0.222838 |
| name_containment_s1 | float32 | 0 | 0 | 1 | 0.35645 | 26 | 0.804209 | 0.270117 |
| name_containment_target | float32 | 0 | 0 | 1 | 0.363582 | 41 | 0.784858 | 0.287973 |
| address_token_intersection | uint16 | 0 | 1 | 25 | 1.74226 | 27 | 6.01018 | 1.19261 |
| address_token_union | uint16 | 3 | 12 | 47 | 5.71029 | 45 | 9.56357 | 14.0452 |
| address_jaccard | float32 | 0 | 0.0837259 | 1 | 0.135768 | 321 | 0.625751 | 0.0906709 |
| address_containment_s1 | float32 | 0 | 0.142851 | 1 | 0.176115 | 185 | 0.731054 | 0.14541 |
| address_containment_target | float32 | 0 | 0.166667 | 1 | 0.188451 | 225 | 0.750568 | 0.164412 |
| name_shared_idf | float32 | 0 | 0 | 1.02968 | 0.00887382 | 4173 | 0.0124762 | 0.00227161 |
| address_shared_idf | float32 | 0 | 0.000732922 | 3.00119 | 0.0220976 | 16265 | 0.0476979 | 0.00361592 |
| shared_address_numbers | uint16 | 0 | 0 | 10 | 0.511315 | 12 | 1.18408 | 0.285647 |
| conflicting_address_numbers | bool | 0 | 1 | 1 | 0.431343 | 2 | 0.242653 | 0.762978 |
| shared_postal_tokens | uint8 | 0 | 0 | 1 | 0.132933 | 2 | 0.0555994 | 0.0172502 |
| conflicting_postal_tokens | bool | 0 | 0 | 1 | 0.0921249 | 2 | 0.00457671 | 0.00863919 |
| digit_sequence_equal | bool | 0 | 0 | 1 | 0.33272 | 2 | 0.620987 | 0.116984 |
| house_number_equal | bool | 0 | 0 | 1 | 0.423779 | 2 | 0.717032 | 0.225092 |
| name_ratio | float32 | 0 | 0.427054 | 1 | 0.219478 | 2077 | 0.821118 | 0.46848 |
| name_partial_ratio | float32 | 0 | 0.533007 | 1 | 0.225988 | 1304 | 0.896577 | 0.568623 |
| name_token_sort_ratio | float32 | 0 | 0.415601 | 1 | 0.221818 | 2021 | 0.835072 | 0.463457 |
| name_token_set_ratio | float32 | 0 | 0.470031 | 1 | 0.255689 | 1699 | 0.896567 | 0.515639 |
| address_ratio | float32 | 0 | 0.395211 | 1 | 0.136327 | 8813 | 0.772193 | 0.408773 |
| address_partial_ratio | float32 | 0 | 0.457922 | 1 | 0.142054 | 5774 | 0.833337 | 0.473312 |
| address_token_sort_ratio | float32 | 0 | 0.413807 | 1 | 0.139414 | 7955 | 0.826956 | 0.425881 |
| address_token_set_ratio | float32 | 0 | 0.430092 | 1 | 0.160172 | 7233 | 0.88411 | 0.450772 |
| s1_script_class | uint8 | 1 | 1 | 1 | 0 | 1 | 1 | 1 |
| target_script_class | uint8 | 0 | 1 | 4 | 0.410668 | 5 | 1.10358 | 1.07119 |
| same_script_class | bool | 0 | 1 | 1 | 0.18929 | 2 | 0.946306 | 0.963111 |
| script_conflict | bool | 0 | 0 | 1 | 0.189007 | 2 | 0.0536285 | 0.0367726 |
