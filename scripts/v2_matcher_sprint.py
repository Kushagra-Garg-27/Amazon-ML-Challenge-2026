"""Versioned research-only V2 matcher sprint entry point."""
from pathlib import Path
import argparse
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.matcher_v2.data import OUT,prepare,materialize,write_once
from er.candidates_v2.runtime import Monitor

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','features','errors','train','decision','assessment','reproduce','report'])
    stage=p.parse_args().stage
    with Monitor(OUT/'tmp') as monitor:
        if stage=='prepare':prepare()
        elif stage=='features':materialize()
        else:
            from er.matcher_v2.experiments import run
            run(stage)
    resource_path=OUT/(stage+'_resources.json')
    if resource_path.exists():
        import time
        resource_path=OUT/(stage+'_resources_'+str(time.time_ns())+'.json')
    values=monitor.result();values['duckdb_threads']=2;values['duckdb_memory_mb']=1000
    write_once(resource_path,values)
