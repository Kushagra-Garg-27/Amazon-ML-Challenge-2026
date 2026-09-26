# Candidate oracle ceilings

These are label-derived ceilings, not matcher performance.

| Policy | Macro F0.5 | Mean precision | Mean recall | Pair recall | All/some/none recovered S1 |
|---|---:|---:|---:|---:|---:|
| final_source_50_50_heavy100 | 0.948629291 | 0.980356503 | 0.884089565 | 0.878083933 | 146,947/56,975/4,332 |
| compact_source_37_38_heavy100 | 0.946593298 | 0.979590171 | 0.879570278 | 0.873322957 | 144,683/59,070/4,501 |
| recall_source_75_75 | 0.952327186 | 0.981812081 | 0.892162635 | 0.886680394 | 150,955/53,288/4,011 |

All 12,277 true singleton S1s predict empty and therefore have oracle accuracy 1.0.

## Full metrics

```json
{
  "final_source_50_50_heavy100": {
    "s1": 220531,
    "macro_f0_5": 0.9486292912064773,
    "mean_precision": 0.9803565031673551,
    "mean_recall": 0.884089565109703,
    "recovered_links": 670785,
    "gt_links": 763919,
    "all_gt_recovered_s1": 146947,
    "some_gt_recovered_s1": 56975,
    "no_gt_recovered_s1": 4332,
    "singletons": 12277,
    "singleton_accuracy": 1.0,
    "pair_recall": 0.8780839329824235,
    "per_country_macro_f0_5": {
      "india": 0.9428765702023344,
      "us": 0.9524391154522771
    },
    "link_breakdown": {
      "rec_s2": 325215,
      "gt_s2": 368894,
      "rec_s3": 345570,
      "gt_s3": 395025,
      "rec_both_addr": 646391,
      "gt_both_addr": 730466,
      "rec_either_missing": 24394,
      "gt_either_missing": 33453
    },
    "missed_links_per_s1": [
      {
        "missed": 0,
        "s1": 159224
      },
      {
        "missed": 1,
        "s1": 39670
      },
      {
        "missed": 2,
        "s1": 14336
      },
      {
        "missed": 3,
        "s1": 5126
      },
      {
        "missed": 4,
        "s1": 1603
      },
      {
        "missed": 5,
        "s1": 455
      },
      {
        "missed": 6,
        "s1": 93
      },
      {
        "missed": 7,
        "s1": 23
      },
      {
        "missed": 8,
        "s1": 1
      }
    ]
  },
  "compact_source_37_38_heavy100": {
    "s1": 220531,
    "macro_f0_5": 0.9465932981524486,
    "mean_precision": 0.9795901709963678,
    "mean_recall": 0.8795702782843187,
    "recovered_links": 667148,
    "gt_links": 763919,
    "all_gt_recovered_s1": 144683,
    "some_gt_recovered_s1": 59070,
    "no_gt_recovered_s1": 4501,
    "singletons": 12277,
    "singleton_accuracy": 1.0,
    "pair_recall": 0.8733229570150762,
    "per_country_macro_f0_5": {
      "india": 0.941055376681083,
      "us": 0.9502608682428023
    },
    "link_breakdown": {
      "rec_s2": 323314,
      "gt_s2": 368894,
      "rec_s3": 343834,
      "gt_s3": 395025,
      "rec_both_addr": 643164,
      "gt_both_addr": 730466,
      "rec_either_missing": 23984,
      "gt_either_missing": 33453
    },
    "missed_links_per_s1": [
      {
        "missed": 0,
        "s1": 156960
      },
      {
        "missed": 1,
        "s1": 40999
      },
      {
        "missed": 2,
        "s1": 14958
      },
      {
        "missed": 3,
        "s1": 5348
      },
      {
        "missed": 4,
        "s1": 1668
      },
      {
        "missed": 5,
        "s1": 475
      },
      {
        "missed": 6,
        "s1": 98
      },
      {
        "missed": 7,
        "s1": 23
      },
      {
        "missed": 8,
        "s1": 2
      }
    ]
  },
  "recall_source_75_75": {
    "s1": 220531,
    "macro_f0_5": 0.9523271857935661,
    "mean_precision": 0.9818120808412423,
    "mean_recall": 0.8921626354430267,
    "recovered_links": 677352,
    "gt_links": 763919,
    "all_gt_recovered_s1": 150955,
    "some_gt_recovered_s1": 53288,
    "no_gt_recovered_s1": 4011,
    "singletons": 12277,
    "singleton_accuracy": 1.0,
    "pair_recall": 0.8866803941255552,
    "per_country_macro_f0_5": {
      "india": 0.945561113008187,
      "us": 0.9568081171872526
    },
    "link_breakdown": {
      "rec_s2": 328232,
      "gt_s2": 368894,
      "rec_s3": 349120,
      "gt_s3": 395025,
      "rec_both_addr": 651774,
      "gt_both_addr": 730466,
      "rec_either_missing": 25578,
      "gt_either_missing": 33453
    },
    "missed_links_per_s1": [
      {
        "missed": 0,
        "s1": 163232
      },
      {
        "missed": 1,
        "s1": 37368
      },
      {
        "missed": 2,
        "s1": 13234
      },
      {
        "missed": 3,
        "s1": 4703
      },
      {
        "missed": 4,
        "s1": 1480
      },
      {
        "missed": 5,
        "s1": 405
      },
      {
        "missed": 6,
        "s1": 87
      },
      {
        "missed": 7,
        "s1": 21
      },
      {
        "missed": 8,
        "s1": 1
      }
    ]
  }
}
```
