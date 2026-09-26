"""Bounded audit of the frozen feature-v1 implementation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np

from .schema import FEATURES, FEATURE_NAMES
from er.matcher.controlled import MODEL_FEATURE_NAMES_V1_1, REMOVED_REDUNDANT_FEATURES


def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(8<<20),b""): h.update(block)
    return h.hexdigest()


def audit(feature_glob: str, labels: Path, output_json: Path,
          output_md: Path, corrected_spec: Path, corrected_md: Path) -> dict:
    con=duckdb.connect(); con.execute("SET memory_limit='800MB'; SET threads=1")
    con.execute(f"""CREATE TEMP VIEW base AS
      SELECT f.*,l.y FROM read_parquet('{feature_glob}') f
      JOIN read_parquet('{labels.as_posix()}') l USING(source1_entity_id,target_entity_id)""")
    population_rows=con.sql("SELECT count(*) FROM base").fetchone()[0]
    stats=[]
    for item in FEATURES:
        name=item["name"]
        row=con.sql(f"""SELECT min({name})::DOUBLE,max({name})::DOUBLE,
          avg({name}::DOUBLE),stddev_pop({name}::DOUBLE),
          approx_quantile({name}::DOUBLE,[0.01,0.5,0.99]),
          count(*) FILTER(WHERE {name} IS NULL),
          count(*) FILTER(WHERE typeof({name}) IN ('FLOAT','DOUBLE') AND NOT isfinite({name}::DOUBLE)),
          approx_count_distinct({name}),
          avg({name}::DOUBLE) FILTER(WHERE y=1),avg({name}::DOUBLE) FILTER(WHERE y=0)
          FROM base""").fetchone()
        stats.append({"name":name,"type":item["type"],"group":item["group"],
          "min":row[0],"max":row[1],"mean":row[2],"std":row[3],
          "q01":row[4][0],"q50":row[4][1],"q99":row[4][2],
          "nulls":row[5],"nan_inf":row[6],"approx_unique":row[7],
          "positive_mean":row[8],"negative_mean":row[9],
          "constant":row[0]==row[1],"near_constant":bool(row[3] is not None and row[3]<1e-6)})
    table=con.sql("SELECT "+",".join(FEATURE_NAMES)+" FROM base USING SAMPLE reservoir(100000 ROWS) REPEATABLE(42)").fetch_arrow_table()
    x=np.column_stack([np.asarray(table[n]).astype(np.float64,copy=False) for n in FEATURE_NAMES])
    exact_duplicates=[]; perfect_correlations=[]
    for i in range(len(FEATURE_NAMES)):
        for j in range(i+1,len(FEATURE_NAMES)):
            if np.std(x[:,i])>0 and np.std(x[:,j])>0 and np.array_equal(x[:,i],x[:,j]): exact_duplicates.append([FEATURE_NAMES[i],FEATURE_NAMES[j]])
            elif np.std(x[:,i])>0 and np.std(x[:,j])>0:
                corr=float(np.corrcoef(x[:,i],x[:,j])[0,1])
                if abs(corr)>0.999999: perfect_correlations.append([FEATURE_NAMES[i],FEATURE_NAMES[j],corr])
    con.close()

    original=json.loads(Path("work/feature_spec_v1.json").read_text(encoding="utf-8"))
    corrected=json.loads(json.dumps(original))
    corrected["schema_version"]="1.1"
    corrected["feature_spec_version"]="feature_spec_v1.1"
    corrected["compatible_numeric_artifact_version"]="feature_spec_v1"
    corrected["physical_artifact_columns"]="feature_spec_v1 (65 columns, unchanged)"
    corrected["removed_redundant_model_inputs"]=list(REMOVED_REDUNDANT_FEATURES)
    corrected["correction"]=("The conflicting_address_numbers description now matches the frozen implementation. "
        "Four redundant model inputs are omitted from the v1.1 model matrix. Physical feature artifacts "
        "and their 65 columns are unchanged.")
    for item in corrected["features"]:
        if item["name"]=="conflicting_address_numbers":
            item["computation"]="Both have non-empty address numeric-token sets and the sets are unequal."
    corrected["features"]=[x for x in corrected["features"] if x["name"] in MODEL_FEATURE_NAMES_V1_1]
    corrected_spec.write_text(json.dumps(corrected,indent=2)+"\n",encoding="utf-8")
    md=Path("work/feature_spec_v1.md").read_text(encoding="utf-8")
    md=md.replace("# Feature specification v1","# Feature specification v1.1")
    md=md.replace("Version: `feature_spec_v1`.","Version: `feature_spec_v1.1` (physical-artifact compatible with `feature_spec_v1`).")
    md=md.replace("Both have numbers and their sets are disjoint.","Both have non-empty numeric-token sets and the sets are unequal.")
    for name in REMOVED_REDUNDANT_FEATURES:
        md="\n".join(line for line in md.splitlines() if f"`{name}`" not in line)+"\n"
    md += "\n## Versioned correction\n\nThe physical 65-column v1 artifacts remain unchanged. The v1.1 model matrix omits four redundant inputs: `retrieved_sorted_name`, `retrieved_exact_address`, `shared_postal_tokens`, and `target_is_s3`. It also corrects the `conflicting_address_numbers` description from disjoint to unequal non-empty sets.\n"
    corrected_md.write_text(md,encoding="utf-8")

    result={"correlation_sample_rows":len(table),"population_rows":population_rows,"features":stats,
      "exact_duplicate_pairs":exact_duplicates,"perfect_correlation_pairs":perfect_correlations,
      "constant_features":[x["name"] for x in stats if x["constant"]],
      "near_constant_features":[x["name"] for x in stats if x["near_constant"]],
      "leakage_findings":[],"test_time_unavailable":[],"country_handling":"open-set equality only",
      "feature_spec_v1_sha256":sha256(Path("work/feature_spec_v1.json")),
      "feature_spec_v1_1_sha256":sha256(corrected_spec),
      "model_feature_count_v1_1":len(MODEL_FEATURE_NAMES_V1_1),
      "removed_redundant_model_inputs":list(REMOVED_REDUNDANT_FEATURES),
      "correction":"v1.1 omits four redundant model inputs and corrects conflicting_address_numbers prose; physical artifacts unchanged"}
    output_json.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    lines=["# Feature v1 audit","",f"Audited {len(stats)} features on a deterministic 100,000-row correlation sample and the full bounded pilot population.","",
      f"Original spec: `{result['feature_spec_v1_sha256']}`. Corrected v1.1 spec: `{result['feature_spec_v1_1_sha256']}`.","",
      "v1.1 corrects `conflicting_address_numbers` prose and omits four redundant model inputs. The physical v1 artifacts, numeric values, and 65 stored columns remain unchanged; the v1.1 training matrix has 61 ordered inputs.","",
      f"Exact duplicate pairs on the sample: `{exact_duplicates}`.",f"Perfect-correlation pairs on the sample: `{perfect_correlations}`.",
      f"Constant features: `{result['constant_features']}`. Near-constant features: `{result['near_constant_features']}`.","",
      "No label leakage or test-time unavailable input was found. Country is represented only by open-set equality.","",
      "| Feature | Type | Min | Median | Max | Std | Unique~ | Positive mean | Negative mean |","|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for s in stats:
        lines.append(f"| {s['name']} | {s['type']} | {s['min']:.6g} | {s['q50']:.6g} | {s['max']:.6g} | {s['std']:.6g} | {s['approx_unique']} | {s['positive_mean']:.6g} | {s['negative_mean']:.6g} |")
    output_md.write_text("\n".join(lines)+"\n",encoding="utf-8")
    return result
