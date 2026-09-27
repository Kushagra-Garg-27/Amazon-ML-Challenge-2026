# Release requirements audit

The authoritative challenge documents require `output/matching_results.tsv` with the exact header `source1_entity_id\tmatched_entity_ids` and `output/candidate_pairs.tsv` with the exact header `source1_entity_id\tcandidate_entity_ids`.

Each raw test S1 must appear exactly once in both files. IDs are preserved verbatim; each comma-separated list contains distinct existing test S2/S3 IDs with no spaces. An empty list is an empty second TSV field after an explicit tab. Candidate pairs are the exact final post-pruning set fed to the model, including score-negative pairs. Matches are the candidates meeting the frozen threshold and must be subsets of candidates.

The archive must be named `<team_name>_submission.zip` and contain the two TSVs, a runnable `code/business_entity_resolution/src/` pipeline, its README and pinned requirements, plus the completed `Documentation_template.md` at the ZIP root. Raw datasets, environments, caches, temporary data, feature and score partitions, secrets, and unrelated experiments are excluded. The model must satisfy the MIT/Apache 2.0 licensing rule. No external identity data or test labels may be used.

The official validator supports `--matching`, `--candidate`, `--test-dir`, and `--check-ids`. The user supplied the team name `broCode`; the archive name is `broCode_submission.zip`.
