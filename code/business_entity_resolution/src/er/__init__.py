"""Business Entity Resolution pipeline (offline, memory-frugal, reproducible).

Package layout (see IMPLEMENTATION_PLAN.md ER-###):
    io          ingestion + int-coding + batched readers
    normalize   deterministic multi-key normalization
    analyze_gt  ground-truth analysis & assumption checks
    split       stratified validation split
    blocking    multi-pass candidate generation
    candidates  union / prune / cap
    features    pairwise feature engineering
    dataset     labeled training-set assembly + hard negatives
    train       model comparison (logistic / LightGBM / XGBoost)
    threshold   F_0.5-optimized decision policy
    predict     per-S1 match-set assembly
    evaluate    macro-F_0.5 scorer
    submit      TSV writers + validator gate
    ledger      append-only experiment tracking
    cli         `python -m er <subcommand>` dispatcher
"""

__version__ = "0.1.0"
