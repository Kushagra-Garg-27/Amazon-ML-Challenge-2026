"""Memory-bounded utilities for controlled LightGBM matcher development."""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import duckdb
import lightgbm as lgb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from er.features.schema import FEATURES, FEATURE_NAMES
from er.features.exact import script_class

SEED = 42
MEMORY_LIMIT = "800MB"

# Feature v1 remains the immutable 65-column physical artifact contract.  v1.1
# removes exact duplicate model inputs discovered by the bounded audit; no
# physical feature values or columns are rewritten.
REMOVED_REDUNDANT_FEATURES = (
    "retrieved_sorted_name", "retrieved_exact_address",
    "shared_postal_tokens", "target_is_s3",
)
MODEL_FEATURE_NAMES_V1_1 = tuple(
    name for name in FEATURE_NAMES if name not in REMOVED_REDUNDANT_FEATURES)
MODEL_FEATURES_V1_1 = tuple(
    item for item in FEATURES if item["name"] in MODEL_FEATURE_NAMES_V1_1)

FEATURE_GROUPS = {
    "exact_provenance": tuple(f["name"] for f in MODEL_FEATURES_V1_1 if f["group"] in {"provenance", "exact"}),
    "exact_token": tuple(f["name"] for f in MODEL_FEATURES_V1_1 if f["group"] in {"provenance", "exact", "token"}),
    "exact_token_numeric_address": tuple(f["name"] for f in MODEL_FEATURES_V1_1 if f["group"] in {"provenance", "exact", "token", "missing_length", "numeric"}),
    "all_non_fuzzy": tuple(f["name"] for f in MODEL_FEATURES_V1_1 if f["group"] != "fuzzy"),
    "all_v1": MODEL_FEATURE_NAMES_V1_1,
    "without_rank_provenance": tuple(f["name"] for f in MODEL_FEATURES_V1_1 if f["group"] != "provenance"),
    "without_address": tuple(f["name"] for f in MODEL_FEATURES_V1_1 if not any(k in f["name"] for k in ("address", "postal", "house", "digit", "numeric"))),
    "without_rapidfuzz": tuple(f["name"] for f in MODEL_FEATURES_V1_1 if f["group"] != "fuzzy"),
}


def validate_model_feature_order(actual: Sequence[str], expected: Sequence[str]) -> bool:
    if tuple(actual) != tuple(expected):
        raise ValueError("LightGBM feature order does not match the frozen matcher policy")
    return True


def assemble_predictions(source_ids: Sequence[str], target_ids: Sequence[str],
                         scores: Sequence[float], threshold: float) -> dict[str,tuple[str,...]]:
    """Assemble deterministic multi-match sets; empty sets are represented by absence."""
    result: dict[str,list[str]] = {}
    for source,target,score in zip(source_ids,target_ids,scores):
        if score >= threshold:
            result.setdefault(source,[]).append(target)
    return {source:tuple(sorted(set(targets))) for source,targets in sorted(result.items())}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{MEMORY_LIMIT}'; SET threads=1; SET preserve_insertion_order=false")
    return con


def build_labels(candidate_glob: str, output: Path, allowed_roles: Sequence[str]) -> dict:
    """Create separate labels only for explicitly allowed development roles."""
    output.parent.mkdir(parents=True, exist_ok=True)
    con = connect()
    roles = ",".join(f"'{x}'" for x in allowed_roles)
    con.execute(f"""
      CREATE TEMP VIEW candidates AS
      SELECT * FROM read_parquet('{candidate_glob}',filename=true)
      WHERE regexp_extract(filename,'([^/\\\\]+)\\.parquet$',1) IS NOT NULL
        AND CASE
          WHEN filename LIKE '%baseline_dev%' THEN 'baseline_dev'
          WHEN filename LIKE '%model_fit%' THEN 'model_fit'
          WHEN filename LIKE '%model_tune%' THEN 'model_tune'
          WHEN filename LIKE '%model_threshold%' THEN 'model_threshold'
        END IN ({roles})
    """)
    con.execute("""
      CREATE TEMP TABLE gt AS
      SELECT source1_entity_id,trim(mid) target_entity_id
      FROM read_csv('dataset/train/train_ground_truth.tsv',delim='\t',header=true,
                    quote='',all_varchar=true),
           unnest(string_split(matched_entity_ids,',')) u(mid)
      WHERE source1_entity_id IN (SELECT DISTINCT source1_entity_id FROM candidates)
        AND matched_entity_ids IS NOT NULL AND length(trim(mid))>0
    """)
    partial = output.with_suffix(".partial.parquet")
    partial.unlink(missing_ok=True)
    con.execute(f"""
      COPY (SELECT c.source1_entity_id,c.target_entity_id,
                   (g.target_entity_id IS NOT NULL)::UTINYINT y
            FROM candidates c LEFT JOIN gt g USING(source1_entity_id,target_entity_id)
            ORDER BY 1,2)
      TO '{partial.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)
    """)
    partial.replace(output)
    row = con.sql(f"SELECT count(*),sum(y),count(distinct source1_entity_id) FROM read_parquet('{output.as_posix()}')").fetchone()
    con.close()
    return {"rows": row[0], "positives": row[1], "s1": row[2], "bytes": output.stat().st_size, "sha256": sha256(output)}


HARD_SCORE = """(
  retrieval_pass_count::DOUBLE*100 + exact_address_norm::INT*80 +
  exact_name_nosuffix::INT*60 + exact_name_sorted::INT*40 +
  name_token_set_ratio*20 + address_token_set_ratio*20 +
  exact_numeric_set::INT*12 + exact_postal_token::INT*12)"""


def build_training_samples(feature_glob: str, labels: Path, selection: Path,
                           out_dir: Path) -> dict:
    """Build current-hybrid and mixed samples for every nested fit tier."""
    out_dir.mkdir(parents=True, exist_ok=True)
    con = connect()
    con.execute(f"""
      CREATE TEMP VIEW fit AS
      SELECT f.*,l.y,s.tier_a,s.tier_b,s.tier_c
      FROM read_parquet('{feature_glob}',filename=true) f
      JOIN read_parquet('{labels.as_posix()}') l USING(source1_entity_id,target_entity_id)
      JOIN read_parquet('{selection.as_posix()}') s ON f.source1_entity_id=s.entity_id
      WHERE s.sample_role LIKE 'model_fit%'
    """)
    results = {}
    tier_pred = {"A": "tier_a", "B": "tier_b", "C": "tier_c"}
    for tier, pred in tier_pred.items():
        for policy in ("current_hybrid", "mixed"):
            path = out_dir / f"{policy}_tier_{tier.lower()}.parquet"
            path.unlink(missing_ok=True)
            if policy == "current_hybrid":
                sql = f"""
                  WITH ranked AS (
                    SELECT *,row_number() OVER(
                      PARTITION BY source1_entity_id,y,target_is_s2
                      ORDER BY {HARD_SCORE} DESC,
                               md5(target_entity_id||':current-hybrid-v1'),target_entity_id) rn
                    FROM fit WHERE {pred})
                  SELECT * EXCLUDE(tier_a,tier_b,tier_c,rn)
                  FROM ranked WHERE y=1 OR rn<=10 ORDER BY source1_entity_id,target_entity_id
                """
            else:
                sql = f"""
                  WITH ranked AS (
                    SELECT *,
                      row_number() OVER(PARTITION BY source1_entity_id,y,target_is_s2
                        ORDER BY {HARD_SCORE} DESC,target_entity_id) hard_rn,
                      row_number() OVER(PARTITION BY source1_entity_id,y,target_is_s2
                        ORDER BY (exact_address_norm OR exact_name_nosuffix OR exact_name_sorted) DESC,
                                 provenance DESC,{HARD_SCORE} DESC,target_entity_id) collision_rn,
                      row_number() OVER(PARTITION BY source1_entity_id,y,target_is_s2
                        ORDER BY md5(target_entity_id||':mixed-v1-42'),target_entity_id) random_rn
                    FROM fit WHERE {pred})
                  SELECT * EXCLUDE(tier_a,tier_b,tier_c,hard_rn,collision_rn,random_rn)
                  FROM ranked
                  WHERE y=1 OR hard_rn<=6 OR
                    ((exact_address_norm OR exact_name_nosuffix OR exact_name_sorted OR retrieval_pass_count>=2) AND collision_rn<=3)
                    OR random_rn<=3
                  ORDER BY source1_entity_id,target_entity_id
                """
            con.execute(f"COPY ({sql}) TO '{path.as_posix()}' (FORMAT PARQUET,COMPRESSION ZSTD)")
            row = con.sql(f"""SELECT count(*),sum(y),count(*)-sum(y),count(distinct source1_entity_id),
              count(*) FILTER(WHERE y=0 AND target_is_s2),count(*) FILTER(WHERE y=0 AND target_is_s3),
              count(*) FILTER(WHERE y=0 AND (s1_address_missing OR target_address_missing))
              FROM read_parquet('{path.as_posix()}')""").fetchone()
            results[f"{policy}_tier_{tier.lower()}"] = dict(zip(
                ("rows","positives","negatives","s1","negative_s2","negative_s3","negative_address_missing"), row))
            results[f"{policy}_tier_{tier.lower()}"] |= {
                "bytes": path.stat().st_size, "sha256": sha256(path), "path": path.as_posix()}
    con.close()
    return results


def load_training(path: Path, features: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
    con = connect()
    cols = ",".join(features)
    table = con.sql(f"SELECT {cols},y FROM read_parquet('{path.as_posix()}') ORDER BY source1_entity_id,target_entity_id").to_arrow_table()
    con.close()
    x = np.column_stack([np.asarray(table[name]).astype(np.float32, copy=False) for name in features])
    y = np.asarray(table["y"]).astype(np.uint8, copy=False)
    return x, y


def train_model(sample: Path, features: Sequence[str], params: dict,
                rounds: int, model_path: Path | None = None,
                valid_sample: Path | None = None, early_stopping: int | None = None) -> tuple[lgb.Booster, dict]:
    stage = {}
    p = dict(params)
    p.update({"objective":"binary","metric":"binary_logloss","seed":SEED,
              "feature_fraction_seed":SEED,"bagging_seed":SEED,"data_random_seed":SEED,
              "num_threads":min(2,int(p.get("num_threads",2))),"verbosity":-1,
              "deterministic":True,"force_col_wise":True})
    t0 = time.time(); x, y = load_training(sample, features); stage["load_seconds"] = time.time()-t0
    stage["matrix_bytes"] = int(x.nbytes + y.nbytes)
    t0 = time.time(); train = lgb.Dataset(x, label=y, feature_name=list(features), params=p, free_raw_data=True); train.construct(); stage["dataset_seconds"] = time.time()-t0
    valid_sets = None; valid_names = None; callbacks = []
    xv = yv = None
    if valid_sample:
        xv, yv = load_training(valid_sample, features)
        valid = lgb.Dataset(xv, label=yv, feature_name=list(features), reference=train, params=p, free_raw_data=True)
        valid_sets=[valid]; valid_names=["model_tune_sample"]
        if early_stopping: callbacks=[lgb.early_stopping(early_stopping,verbose=False)]
    t0 = time.time()
    model = lgb.train(p,train,num_boost_round=rounds,valid_sets=valid_sets,
                      valid_names=valid_names,callbacks=callbacks)
    stage["training_seconds"] = time.time()-t0
    stage["rows"] = len(y); stage["positives"] = int(y.sum()); stage["features"] = len(features)
    stage["best_iteration"] = int(model.best_iteration or model.current_iteration())
    if model_path:
        model.save_model(model_path.as_posix(),num_iteration=stage["best_iteration"])
        stage["model_bytes"] = model_path.stat().st_size
        stage["model_sha256"] = sha256(model_path)
    del x,y,xv,yv,train
    return model, stage


def score_parts(model: lgb.Booster, feature_files: Iterable[Path], output_dir: Path,
                features: Sequence[str], batch_size: int = 200_000) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    results=[]; total=0; started=time.time()
    schema=pa.schema([pa.field("source1_entity_id",pa.string(),False),
                      pa.field("target_entity_id",pa.string(),False),
                      pa.field("score",pa.float32(),False),
                      pa.field("target_is_s2",pa.bool_(),False),
                      pa.field("address_missing",pa.bool_(),False),
                      pa.field("script_conflict",pa.bool_(),False),
                      pa.field("numeric_conflict",pa.bool_(),False)])
    for source in sorted(feature_files):
        dest=output_dir/source.name; partial=dest.with_suffix(".partial.parquet")
        partial.unlink(missing_ok=True); writer=pq.ParquetWriter(partial,schema,compression="zstd")
        rows=0; t0=time.time()
        try:
            pf=pq.ParquetFile(source)
            metadata=("target_is_s2","s1_address_missing","target_address_missing","script_conflict",
                      "conflicting_address_numbers","conflicting_postal_tokens")
            columns=list(dict.fromkeys(["source1_entity_id","target_entity_id",*metadata,*features]))
            for batch in pf.iter_batches(batch_size=batch_size,columns=columns):
                x=np.column_stack([np.asarray(batch[name]).astype(np.float32,copy=False) for name in features])
                score=model.predict(x,num_iteration=model.best_iteration or model.current_iteration()).astype(np.float32)
                address=np.asarray(batch["s1_address_missing"]).astype(bool) | np.asarray(batch["target_address_missing"]).astype(bool)
                numeric=np.asarray(batch["conflicting_address_numbers"]).astype(bool) | np.asarray(batch["conflicting_postal_tokens"]).astype(bool)
                writer.write_table(pa.table({"source1_entity_id":batch["source1_entity_id"],
                                             "target_entity_id":batch["target_entity_id"],
                                             "score":score,
                                             "target_is_s2":batch["target_is_s2"],
                                             "address_missing":address,
                                             "script_conflict":batch["script_conflict"],
                                             "numeric_conflict":numeric},schema=schema))
                rows+=len(batch)
        finally:
            writer.close()
        partial.replace(dest); total+=rows
        results.append({"input":source.as_posix(),"output":dest.as_posix(),"rows":rows,
                        "bytes":dest.stat().st_size,"sha256":sha256(dest),"seconds":time.time()-t0})
    return {"rows":total,"seconds":time.time()-started,"parts":results}


@dataclass
class Population:
    scores: np.ndarray
    entity: np.ndarray
    y: np.ndarray
    s2: np.ndarray
    address_missing: np.ndarray
    script_conflict: np.ndarray
    numeric_conflict: np.ndarray
    truth_n: np.ndarray
    recovered_n: np.ndarray
    countries: np.ndarray
    entity_ids: tuple[str, ...]
    truth_breakdown: dict[str,int]


def load_population(score_glob: str, label_path: Path,
                    entity_ids: Sequence[str]) -> Population:
    """Load compact encoded evaluation arrays; raw strings are discarded."""
    con=connect()
    con.execute("CREATE TEMP TABLE entities(entity_id VARCHAR)")
    con.executemany("INSERT INTO entities VALUES (?)",[(x,) for x in entity_ids])
    con.execute(f"""CREATE TEMP VIEW rows AS
      SELECT s.source1_entity_id,s.target_entity_id,s.score,l.y,
             s.target_is_s2,s.address_missing,s.script_conflict,s.numeric_conflict
      FROM read_parquet('{score_glob}') s
      JOIN read_parquet('{label_path.as_posix()}') l USING(source1_entity_id,target_entity_id)
      JOIN entities e ON s.source1_entity_id=e.entity_id
    """)
    table=con.sql("SELECT * FROM rows ORDER BY source1_entity_id,target_entity_id").to_arrow_table()
    index={x:i for i,x in enumerate(entity_ids)}
    entity=np.fromiter((index[x.as_py()] for x in table["source1_entity_id"]),dtype=np.int32,count=len(table))
    scores=np.asarray(table["score"]).astype(np.float32,copy=False)
    y=np.asarray(table["y"]).astype(np.uint8,copy=False)
    s2=np.asarray(table["target_is_s2"]).astype(bool,copy=False)
    address=np.asarray(table["address_missing"]).astype(bool,copy=False)
    script=np.asarray(table["script_conflict"]).astype(bool,copy=False)
    numeric=np.asarray(table["numeric_conflict"]).astype(bool,copy=False)
    recovered=np.bincount(entity,weights=y,minlength=len(entity_ids)).astype(np.int32)
    con.execute("""CREATE TEMP TABLE gt AS SELECT source1_entity_id,trim(mid) mid
      FROM read_csv('dataset/train/train_ground_truth.tsv',delim='\t',header=true,quote='',all_varchar=true),
      unnest(string_split(matched_entity_ids,',')) u(mid)
      WHERE source1_entity_id IN (SELECT entity_id FROM entities)
        AND matched_entity_ids IS NOT NULL AND length(trim(mid))>0""")
    truth=dict(con.sql("SELECT e.entity_id,count(g.mid) FROM entities e LEFT JOIN gt g ON e.entity_id=g.source1_entity_id GROUP BY 1").fetchall())
    country=dict(con.sql("SELECT e.entity_id,k.country_norm FROM entities e JOIN read_parquet('work/keys/train_s1.parquet') k ON e.entity_id=k.entity_id").fetchall())
    breakdown={}
    breakdown["s2"]=con.sql("SELECT count(*) FROM gt WHERE starts_with(mid,'S2-')").fetchone()[0]
    breakdown["s3"]=con.sql("SELECT count(*) FROM gt WHERE starts_with(mid,'S3-')").fetchone()[0]
    addr=con.sql("""SELECT count(*) FILTER(WHERE length(s.addr_norm)=0 OR length(t.addr_norm)=0),count(*)
      FROM gt g JOIN read_parquet('work/keys/train_s1.parquet') s ON g.source1_entity_id=s.entity_id
      JOIN (SELECT entity_id,name_norm,addr_norm FROM read_parquet('work/keys/train_s2.parquet')
            UNION ALL SELECT entity_id,name_norm,addr_norm FROM read_parquet('work/keys/train_s3.parquet')) t ON g.mid=t.entity_id""").fetchone()
    breakdown["address_missing"]=addr[0]; breakdown["address_present"]=addr[1]-addr[0]
    names=con.sql("""SELECT s.name_norm,t.name_norm FROM gt g
      JOIN read_parquet('work/keys/train_s1.parquet') s ON g.source1_entity_id=s.entity_id
      JOIN (SELECT entity_id,name_norm FROM read_parquet('work/keys/train_s2.parquet')
            UNION ALL SELECT entity_id,name_norm FROM read_parquet('work/keys/train_s3.parquet')) t ON g.mid=t.entity_id""").fetchall()
    breakdown["script_conflict"]=sum(bool(script_class(a) and script_class(b) and script_class(a)!=script_class(b)) for a,b in names)
    breakdown["script_same_or_empty"]=len(names)-breakdown["script_conflict"]
    truth_n=np.array([truth[x] for x in entity_ids],dtype=np.int16)
    countries=np.array([country[x] for x in entity_ids])
    con.close(); del table
    return Population(scores,entity,y,s2,address,script,numeric,truth_n,recovered,countries,tuple(entity_ids),breakdown)


def _macro(tp: np.ndarray,pred: np.ndarray,truth: np.ndarray) -> float:
    p=np.divide(tp,pred,out=np.where(truth==0,1.0,0.0),where=pred>0)
    r=np.divide(tp,truth,out=np.ones_like(tp,dtype=float),where=truth>0)
    denom=.25*p+r
    return float(np.mean(np.divide(1.25*p*r,denom,out=np.zeros_like(p,dtype=float),where=denom>0)))


def policy_mask(pop: Population, threshold: float, reject_numeric_conflict: bool=False,
                source_thresholds: tuple[float,float] | None=None) -> np.ndarray:
    if source_thresholds is None:
        take=pop.scores>=threshold
    else:
        s2_threshold,s3_threshold=source_thresholds
        take=np.where(pop.s2,pop.scores>=s2_threshold,pop.scores>=s3_threshold)
    if reject_numeric_conflict:
        take=take & ~pop.numeric_conflict
    return take


def source_threshold_gap(pop: Population) -> dict:
    """Tune-only pair-level calibration diagnostic; it does not select a threshold."""
    result={}
    for key,mask in (("s2",pop.s2),("s3",~pop.s2)):
        rows=[]
        for threshold in [x/20 for x in range(21)]:
            take=(pop.scores>=threshold)&mask
            tp=int((take&(pop.y==1)).sum()); pred=int(take.sum()); truth=pop.truth_breakdown[key]
            precision=tp/pred if pred else 0.0; recall=tp/truth if truth else 0.0
            denom=.25*precision+recall
            f=.0 if denom==0 else 1.25*precision*recall/denom
            rows.append((f,threshold,precision,recall))
        best=max(rows,key=lambda x:(x[0],-x[1]))
        result[key]={"threshold":best[1],"pair_f0_5":best[0],"precision":best[2],"recall":best[3]}
    result["absolute_gap"]=abs(result["s2"]["threshold"]-result["s3"]["threshold"])
    result["eligible"]=result["absolute_gap"]>=.10
    return result


def evaluate(pop: Population, threshold: float, reject_numeric_conflict: bool=False,
             source_thresholds: tuple[float,float] | None=None) -> dict:
    take=policy_mask(pop,threshold,reject_numeric_conflict,source_thresholds)
    pred=np.bincount(pop.entity[take],minlength=len(pop.truth_n)).astype(np.int32)
    true_take=take & (pop.y==1)
    tpv=np.bincount(pop.entity[true_take],minlength=len(pop.truth_n)).astype(np.int32)
    tp=int(tpv.sum()); total_pred=int(pred.sum()); total_truth=int(pop.truth_n.sum())
    out={"threshold":float(threshold),"macro_f0_5":_macro(tpv,pred,pop.truth_n),
         "recovered_truth_macro_f0_5":_macro(tpv,pred,pop.recovered_n),
         "pair_precision":tp/total_pred if total_pred else 0.0,
         "pair_recall":tp/total_truth if total_truth else 0.0,
         "recovered_pair_recall":tp/int(pop.recovered_n.sum()) if pop.recovered_n.sum() else 0.0,
         "avg_predicted":total_pred/len(pred),"empty_prediction_rate":float(np.mean(pred==0)),
         "singleton_accuracy":float(np.mean(pred[pop.truth_n==0]==0)) if np.any(pop.truth_n==0) else 0.0}
    for country in sorted(set(pop.countries)):
        mask=pop.countries==country; out[f"{country}_macro_f0_5"]=_macro(tpv[mask],pred[mask],pop.truth_n[mask])
    for key,mask in (("s2",pop.s2),("s3",~pop.s2)):
        den=pop.truth_breakdown[key]; hit=int((true_take&mask).sum()); chosen=int((take&mask).sum())
        out[f"{key}_precision"]=hit/chosen if chosen else 0.0; out[f"{key}_recall"]=hit/den if den else 0.0
    for key,mask in (("address_missing",pop.address_missing),("address_present",~pop.address_missing),
                     ("script_conflict",pop.script_conflict),("script_same_or_empty",~pop.script_conflict)):
        den=pop.truth_breakdown[key]; hit=int((true_take&mask).sum())
        out[f"{key}_recall"]=hit/den if den else 0.0
    unique,counts=np.unique(pred,return_counts=True)
    out["prediction_count_distribution"]={str(int(k)):int(v) for k,v in zip(unique,counts)}
    return out


def threshold_search(pop: Population, reject_numeric_conflict: bool=False) -> tuple[dict,list[dict]]:
    coarse=[x/20 for x in range(21)]
    rows=[evaluate(pop,x,reject_numeric_conflict=reject_numeric_conflict) for x in coarse]
    center=max(rows,key=lambda r:(r["macro_f0_5"],-r["threshold"]))["threshold"]
    fine=sorted(set(max(0.0,min(1.0,round(center+d/100,10))) for d in range(-5,6)))
    fine_rows=[evaluate(pop,x,reject_numeric_conflict=reject_numeric_conflict) for x in fine if x not in coarse]
    rows.extend(fine_rows); rows.sort(key=lambda r:r["threshold"])
    best=max(rows,key=lambda r:(r["macro_f0_5"],-r["threshold"]))
    return best,rows


DEFAULT_PARAMS={"learning_rate":.05,"num_leaves":31,"max_depth":8,
                "min_data_in_leaf":100,"feature_fraction":.9,
                "bagging_fraction":.9,"bagging_freq":1,
                "lambda_l2":1.0,"num_threads":2}


def entity_ids_for_role(selection: Path, role_like: str) -> list[str]:
    con=connect(); rows=con.sql(f"SELECT entity_id FROM read_parquet('{selection.as_posix()}') WHERE sample_role LIKE '{role_like}' ORDER BY entity_id").fetchall(); con.close(); return [x[0] for x in rows]


def save_curve(rows: list[dict], output: Path, extra: dict | None=None) -> None:
    payload=[]
    for row in rows:
        x={k:v for k,v in row.items() if k!="prediction_count_distribution"}
        if extra: x.update(extra)
        payload.append(x)
    pq.write_table(pa.Table.from_pylist(payload),output,compression="zstd")
