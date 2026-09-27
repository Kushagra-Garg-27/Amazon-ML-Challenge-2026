"""Research-scope integrity audit without opening sealed or test artifacts."""
from pathlib import Path
import json,sys,subprocess
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.matcher_v2.data import OUT,allocation,write_once,FEATURES
from er.matcher_v2.access import research_ids,sha,role_for,v1_tracked_changes
from er.matcher_v2.experiments import validate_selected_artifacts,active_policy_path
from v2_phase2_integrity import EXPECTED

a=allocation();ids=a['s1'].to_pylist();roles=np.asarray(a['role'])
if len(set(ids))!=100000 or set(ids)!=set(research_ids(ROOT)):raise PermissionError('Research allocation differs')
if not np.array_equal(roles,np.array([role_for(i) for i in ids])):raise PermissionError('Roles differ')
frozen={name:{'expected':expected,'actual':sha(ROOT/name)} for name,expected in EXPECTED.items()}
if not all(x['expected']==x['actual'] for x in frozen.values()):raise RuntimeError('Frozen V1 changed')
changes=subprocess.check_output(['git','diff','--name-status','release_v1','--','code/business_entity_resolution/src/er','scripts'],cwd=ROOT,text=True).splitlines()
tracked=v1_tracked_changes(changes)
if tracked:raise RuntimeError('Tracked V1 source changed')
selected=validate_selected_artifacts(require_policy=True)
features=json.loads((OUT/'features/manifest.json').read_text())
if features['positive_coverage_of_retrieved']!=1.:raise RuntimeError('Positive loss')
if features['feature_spec_sha256']!=sha(OUT/'feature_spec.json'):raise RuntimeError('Feature definition changed')
result={'status':'PASS','scope':'research membership, frozen artifact hashes and sprint manifests only',
 'authorized_unique_s1':len(ids),'roles':{str(i):int((roles==i).sum()) for i in range(4)},
 'frozen_v1':frozen,'tracked_v1_changes':tracked,
 'retrieved_train_positives':features['retrieved_train_positives'],'retrieved_positive_coverage':1.,
 'unretrieved_positives':'not synthesized into candidate features; retained in metric denominators',
 'selected_model_SHA':selected['artifact_SHA'],'decision_file':active_policy_path().name,'decision_SHA':sha(active_policy_path()),
 'excluded_model_columns':sorted(set(['label','y_train','entity_index','role','source1_entity_id','target_entity_id'])-set(FEATURES)),
 'access_controls':['research membership asserted before each stage','training labels only for negative mining',
   'pair feature/model choices use model_select only','utility meta-training uses even policy and optionally model_select entities',
   'calibration even policy indices; decisions odd indices',
   'residual models fit model_select only; even policy early stopping; odd policy decision selection; explicitly reused development data',
   'model bytes, model manifest and feature spec checked before assessment labels',
   'no post-assessment tuning','all complete candidate sets and complete truth denominators for evaluation'],
 'sprint_sealed_membership_decoded':False,'sprint_sealed_labels_read':False,'real_test_rows_read':False,
 'submission_outputs_read':False,'external_enrichment':False,'GT_assignment_features':False,
 'prior_incident':'Earlier Phase 2 decoded sealed source-membership IDs once; no sealed labels/outcomes or real test rows were read. Preserved in work/v2_access_ledger.jsonl.',
 'limitation':'Application-level assertions and code review; not an OS-enforced file-read trace. Research population had previous aggregate exposure, so internal assessment is not an untouched challenge holdout.'}
write_once(OUT/'leakage_audit.json',result)
print(json.dumps({'status':'PASS','research_s1':len(ids),'frozen_v1_checked':len(frozen)}))
