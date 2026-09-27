# Bounded test smoke gate

**PASS.** The deterministic selection contained 20 S1 records each from France, India, and US. It produced 3,102, 3,114, and 3,273 final candidates respectively. Every candidate had one physical feature row and one finite frozen-model score; 225 pairs met the fixed 0.61 threshold. The score artifacts include 116 candidate pairs with a missing target address. The France group passed the same normalization, retrieval, feature and model code path as the other countries.

The three candidate lists and three matching lists merged to one row for each of the 60 S1s. Five prediction lists were empty and retained an explicit tab. The byte-identical packaged challenge validator passed with `--check-ids` on the bounded fixture (60 required S1s, 9,489 valid target IDs, zero blocking issues). No smoke score was interpreted as accuracy.

The sample happened to contain no zero-candidate S1; the empty-field and zero-candidate code paths are tested by the synthetic fixture and release unit tests. Full test zero-candidate counts are recorded in the subsequent candidate audit.
