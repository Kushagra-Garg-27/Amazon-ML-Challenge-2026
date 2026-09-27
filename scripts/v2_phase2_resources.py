"""Project research candidate densities with measured V1 full-test stage receipts."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.runtime import sha,write_json

OUT=ROOT/'work/v2_phase2_r1'
TEST_S1=1_732_544
V1_RESEARCH=15_649_461
PLUS_RESEARCH=18_895_613
V1_TEST_CANDIDATES=273_502_145


def stage(name:str) -> dict:
    files=sorted((ROOT/'work'/name).glob('*.json'))
    if len(files)!=96:raise RuntimeError(f'Historical V1 aggregate receipt count differs: {name}')
    rows=[json.loads(x.read_text()) for x in files]
    return {'receipts':96,'rows':sum(x['rows'] for x in rows),
      'bytes':sum(x['bytes'] for x in rows),
      'wall_seconds':sum(x['wall_seconds'] for x in rows),
      'peak_process_rss_bytes':max((x.get('peak_process_rss_bytes') or 0) for x in rows),
      'peak_temp_bytes':max((x.get('peak_temp_bytes') or 0) for x in rows),
      'receipt_paths':[x.relative_to(ROOT).as_posix() for x in files]}


def project(research_candidates:int,measured:dict,extra_runtime_seconds:float|None=None,
            extra_runtime_basis:dict|None=None) -> dict:
    # This exact research-S1 density projection is the established V2 method.
    projected=round(research_candidates*TEST_S1/100000)
    ratio=projected/V1_TEST_CANDIDATES
    stages={}
    for name,x in measured.items():
        stages[name]={'projected_bytes':round(x['bytes']*ratio),
          'projected_wall_seconds':x['wall_seconds']*ratio,
          'measured_v1_peak_rss_bytes':x['peak_process_rss_bytes'],
          'measured_v1_peak_temp_bytes':x['peak_temp_bytes']}
    baseline_runtime=sum(x['wall_seconds'] for x in measured.values())
    runtime=sum(x['projected_wall_seconds'] for x in stages.values())
    full_runtime=runtime+extra_runtime_seconds if extra_runtime_seconds is not None else None
    return {'research_candidates':research_candidates,'projected_test_candidates':projected,
      'density_per_research_s1':research_candidates/100000,
      'test_s1':TEST_S1,'stages':stages,
      'projected_candidate_parquet_bytes':stages['test_candidates']['projected_bytes'],
      'projected_feature_parquet_bytes':stages['test_features']['projected_bytes'],
      'projected_score_parquet_bytes':stages['test_scores']['projected_bytes'],
      'projected_peak_temp_bytes':max(x['measured_v1_peak_temp_bytes'] for x in stages.values())*ratio,
      'expected_peak_rss_bytes':max(x['measured_v1_peak_rss_bytes'] for x in stages.values()),
      'projected_feature_runtime_seconds':stages['test_features']['projected_wall_seconds'],
      'projected_scoring_runtime_seconds':stages['test_scores']['projected_wall_seconds'],
      'projected_total_stage_runtime_seconds':runtime,
      'runtime_ratio_vs_measured_v1':runtime/baseline_runtime,
      'projected_incremental_generation_runtime_seconds':extra_runtime_seconds,
      'incremental_generation_runtime_basis':extra_runtime_basis,
      'projected_full_runtime_seconds':full_runtime,
      'full_runtime_ratio_vs_measured_v1':full_runtime/baseline_runtime if full_runtime is not None else None,
      'candidate_gate_le_400m':projected<=400_000_000,
      'runtime_gate_le_1p5x_v1':full_runtime<=1.5*baseline_runtime if full_runtime is not None else None,
      'rss_scope':'measured V1 per-stage peak, scaled row throughput does not imply higher peak; V2 peak remains unverified',
      'temporary_disk_scope':'V1 measured per-stage peak linearly projected; V2 indexing additional temp reported separately'}


def run() -> None:
    measured={x:stage(x) for x in ('test_candidates','test_features','test_scores')}
    if any(x['rows']!=V1_TEST_CANDIDATES for x in measured.values()):
        raise RuntimeError('Historical V1 test receipt candidate count differs')
    policies={'v1_research':V1_RESEARCH,'v1_plus_all':PLUS_RESEARCH}
    generation={}
    for cap in (1000,5000):
        index=OUT/f'neighbor_index_df{cap}_resources.json'
        grid=OUT/f'expansion_grid_df{cap}_resources.json'
        if index.exists() and grid.exists():
            # The complete training target index is built once. Seed expansion
            # scales with the number of S1 queries under the density method.
            i=json.loads(index.read_text())['wall_seconds']
            g=json.loads(grid.read_text())['wall_seconds']
            generation[cap]={'one_time_complete_target_index_seconds':i,
              'research_grid_seconds':g,'test_scaled_grid_seconds':g*TEST_S1/100000,
              'projected_incremental_seconds':i+g*TEST_S1/100000,
              'method':'complete target index once + research seed-grid runtime times test/research S1 ratio'}
    expansion=OUT/'expansion_evaluation.json'
    if expansion.exists():
        for name,item in json.loads(expansion.read_text())['configurations'].items():
            policies[name]=item['union_candidate_count']
    cap=OUT/'cap_evaluation.json'
    cap_names=set()
    if cap.exists():
        for name,item in json.loads(cap.read_text())['configurations'].items():
            policies[name]=item['union_candidate_count']
            cap_names.add(name)
    cap_resource=OUT/'cap_sweep_resources.json'
    if cap_resource.exists():
        sweep=json.loads(cap_resource.read_text())['wall_seconds']
        generation['cap_sweep']={'research_full_grid_seconds':sweep,
          'test_scaled_full_grid_seconds':sweep*TEST_S1/100000,
          'projected_incremental_seconds':sweep*TEST_S1/100000,
          'method':'conservative full research cap-sweep runtime times test/research S1 ratio'}
    projections={}
    for name,count in policies.items():
        df_cap=None
        if name.endswith('_df1000'):df_cap=1000
        elif name.endswith('_df5000'):df_cap=5000
        extra=generation.get(df_cap)
        if name in cap_names:extra=generation.get('cap_sweep')
        projections[name]=project(count,measured,
          extra['projected_incremental_seconds'] if extra else None,extra)
    output={'status':'V2_RESEARCH_PROJECTION_WITH_GENERATION','method':'research candidates / 100000 * 1732544',
      'explicit_baseline':'v1_plus_all','test_data_rows_read':False,
      'historical_aggregate_receipts_only':True,
      'measured_v1_test_stages':{k:{x:v for x,v in info.items() if x!='receipt_paths'}
        for k,info in measured.items()},
      'measured_v1_test_stage_runtime_seconds':sum(x['wall_seconds'] for x in measured.values()),
      'policies':projections,
      'measured_generation_runtime':generation,
      'limitations':['Country mix and target distribution may differ between research and test.',
        'Expansion grid runtime is conservatively extrapolated from the entire research grid to every test S1, even for a single selected configuration.',
        'Cap generation runtime uses the full research sweep scaled to test S1; selected-policy generation could be cheaper.',
        'Peak V2 RSS must be measured before production eligibility; V1 peak is a lower-information proxy.'],
      'inputs':{'v1_test_candidate_audit_sha256':sha(ROOT/'work/test_candidate_audit.json'),
        'v1_plus_all_candidate_audit_sha256':sha(ROOT/'work/v2_research/v1_plus_all_structural_audit.json')}}
    preliminary=OUT/'resource_projection.json'
    if preliminary.exists():
        output['preliminary_projection_sha256']=sha(preliminary)
    write_json(OUT/'resource_projection_with_generation.json',output)
    print(json.dumps({k:{'projected_test_candidates':v['projected_test_candidates'],
      'full_runtime_ratio_vs_measured_v1':v['full_runtime_ratio_vs_measured_v1'],
      'candidate_gate_le_400m':v['candidate_gate_le_400m'],
      'runtime_gate_le_1p5x_v1':v['runtime_gate_le_1p5x_v1']}
      for k,v in output['policies'].items()},indent=2))


if __name__=='__main__':run()
