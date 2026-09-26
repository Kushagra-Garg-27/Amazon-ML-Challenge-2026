"""Feature identity, schema, numeric and label audits."""
from __future__ import annotations
import argparse,json,hashlib
from pathlib import Path
import duckdb
from .schema import FEATURE_NAMES

def audit(candidates: Path,features: Path,labels: Path) -> dict:
 c=duckdb.connect(); c.execute("SET memory_limit='800MB'; SET threads=1")
 cp=(candidates/'*.parquet').as_posix(); fp=(features/'*.parquet').as_posix()
 result={}
 result['input_rows']=c.sql(f"select count(*) from read_parquet('{cp}')").fetchone()[0]
 result['feature_rows']=c.sql(f"select count(*) from read_parquet('{fp}')").fetchone()[0]
 result['added']=c.sql(f"select count(*) from read_parquet('{fp}') f anti join read_parquet('{cp}') c using(source1_entity_id,target_entity_id)").fetchone()[0]
 result['removed']=c.sql(f"select count(*) from read_parquet('{cp}') c anti join read_parquet('{fp}') f using(source1_entity_id,target_entity_id)").fetchone()[0]
 result['duplicates']=c.sql(f"select count(*)-count(distinct(source1_entity_id,target_entity_id)) from read_parquet('{fp}')").fetchone()[0]
 float_names=[x for x in FEATURE_NAMES if x.endswith(('ratio','jaccard','containment_s1','containment_target','relative_diff','shared_idf'))]
 result['null_cells']=c.sql(f"select {'+'.join(f'count(*) filter(where {x} is null)' for x in FEATURE_NAMES)} from read_parquet('{fp}')").fetchone()[0]
 result['nan_inf_cells']=c.sql(f"select {'+'.join(f'count(*) filter(where not isfinite({x}))' for x in float_names)} from read_parquet('{fp}')").fetchone()[0]
 c.execute("""CREATE TEMP TABLE pilot_gt AS SELECT source1_entity_id s1,trim(mid) mid
   FROM read_csv('dataset/train/train_ground_truth.tsv',delim='\t',header=true,quote='',all_varchar=true),
   unnest(string_split(matched_entity_ids,',')) u(mid)
   WHERE matched_entity_ids IS NOT NULL AND length(trim(mid))>0
     AND source1_entity_id IN (SELECT DISTINCT source1_entity_id FROM read_parquet(?))""",[cp])
 labels.unlink(missing_ok=True)
 c.execute(f"copy (select c.source1_entity_id,c.target_entity_id,(g.mid is not null)::utinyint y from read_parquet('{cp}') c left join pilot_gt g on c.source1_entity_id=g.s1 and c.target_entity_id=g.mid order by 1,2) to '{labels.as_posix()}' (format parquet,compression zstd)")
 result['positives']=c.sql(f"select sum(y) from read_parquet('{labels.as_posix()}')").fetchone()[0]; result['negatives']=result['input_rows']-result['positives']; result['equal']=result['added']==result['removed']==result['duplicates']==0
 c.close(); return result

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--candidates',type=Path,default=Path('work/feature_pilot_candidates')); ap.add_argument('--features',type=Path,default=Path('work/feature_pilot')); ap.add_argument('--labels',type=Path,default=Path('work/feature_pilot_labels.parquet')); ap.add_argument('--output',type=Path,default=Path('work/feature_pilot_audit.json')); a=ap.parse_args(); out=audit(a.candidates,a.features,a.labels); a.output.write_text(json.dumps(out,indent=2)+"\n"); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
