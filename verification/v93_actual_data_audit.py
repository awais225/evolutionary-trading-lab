from pathlib import Path
import os,sys,shutil,json,hashlib,time,subprocess
repo=Path('/home/user/v93-app');root=Path('/home/user/.cache/v93-data-audit-final')
if not root.exists():
 shutil.copytree(repo/'DATA',root,ignore=shutil.ignore_patterns('*.db','*.db-wal','*.db-shm','logs','BACKUPS','features','data_cache'))
 (root/'DATABASE').mkdir(exist_ok=True)
 import sqlite3
 source=sqlite3.connect('file:'+str(repo/'DATA/DATABASE/lab_state.db')+'?mode=ro',uri=True);dest=sqlite3.connect(root/'DATABASE/lab_state.db');source.backup(dest);dest.close();source.close()
os.environ['EVOLUTIONARY_LAB_DATA_ROOT']=str(root);os.environ['EVOLUTIONARY_LAB_STARTUP_BLOCKING']='1';sys.path.insert(0,str(repo/'backend'))
import sqlite3
rawcon=sqlite3.connect('file:'+str(root/'DATABASE/lab_state.db')+'?mode=ro',uri=True);rawcounts=dict(rawcon.execute('SELECT status,COUNT(*) FROM strategies GROUP BY status'));rawcon.close()
from app.db.database import get_db
from app.research.startup_registry import ensure_ready
from app.deeptest import store as S,scenarios as SC
from app.api.routes import nodes_index
from app.data.engine import get_data_engine
from app.deeptest.coverage import _mstore,pair_coverage
from fastapi.testclient import TestClient
from app.main import app
result={'logical_snapshot_raw_status_counts':rawcounts,'environment':'Linux isolated COPY of original persisted DATA; no production data writes, no native MT5, no orders','files':[]}
db=get_db();ensure_ready(db);S.ensure_schema(db)
result['qualified_before_tabs']=nodes_index(limit=0,sort_by='canonical_win_rate',sort_desc=True)['total']
client=TestClient(app)
for tf,name in [('M1','M1'),('M5','M5'),('M15','M15'),('M30','M30'),('H1','H1'),('H4','H4'),('D1','Daily')]:
 p=Path('/home/user/uploads')/f'XAUUSD_{name}.csv';raw=p.read_bytes();original=repo/'DATA/HISTORICAL/baseline'/p.name
 assert raw==original.read_bytes(),'original file differs'
 before=pair_coverage('XAUUSD',tf)
 validation=client.post('/api/deeptest/csv/validate',params={'symbol':'XAUUSD','timeframe':tf,'filename':p.name,'source_timezone':'ICMARKETS_NY_CLOSE'},content=raw).json()
 assert validation.get('ok'),validation
 report=validation.get('report') or validation
 commit=client.post('/api/deeptest/csv/commit',json={'symbol':'XAUUSD','timeframe':tf,'validation_token':validation['validation_token'],'timezone_confirmed':'ICMARKETS_NY_CLOSE'}).json()
 assert commit.get('ok'),commit
 store=_mstore('XAUUSD',tf);frame=store.read_range();unique=frame.ts.nunique()
 assert len(frame)==unique==report['rows_valid']
 did=get_data_engine()._materialize_dataset_view(store);queried=get_data_engine().get_frame(did)
 assert len(queried)==unique
 archived=root/commit['original_artifact'];assert archived.read_bytes()==raw
 record={'file':p.name,'sha256':hashlib.sha256(raw).hexdigest(),'original_versioned_identical':True,'symbol':'XAUUSD','timeframe':tf,
 'validation':{k:report.get(k) for k in ('rows_read','rows_valid','rows_rejected','rows_duplicate','mapping','timestamp_mode','timezone_source','earliest','latest','earliest_ts','latest_ts','gaps','timeframe_mismatch')},
 'merge':commit['merge'],'existing_before':commit['existing_before'],'queryable_after':unique,'dataset_id':did,'dataset_fingerprint':store.fingerprint,'actual_deep_engine_query_rows':len(queried),'original_archive_verified':True,'quality':commit.get('coverage_after',{}).get('quality')}
 result['files'].append(record);print(json.dumps(record),flush=True)
# First-load endpoint count, without a fetch prerequisite; selected range is exact.
from app.api.routes import deeptest_coverage_pairs
end=float(_mstore('XAUUSD','M1').last_ts)+60;start=end-3*86400
coverage=deeptest_coverage_pairs({'pairs':[{'symbol':'XAUUSD','timeframe':'M1'}],'range_start':start,'range_end':end})
result['initial_range_count']=coverage['coverage']['XAUUSD|M1']
request={'node_id':718,'requested_start':start,'requested_end':end,'node_settings':{'max_trades':40}}
pf=SC.preflight(db,node_id=718,requested_start=start,requested_end=end,node_settings={'max_trades':40})
assert pf['ok'],pf
result['historical_run_preflight']={k:pf.get(k) for k in ('ok','needs_confirmation','may_proceed','period')}
if pf['needs_confirmation']:
 # Isolated automated acceptance only; not a claim that the operator clicked.
 SC.record_decision(db,confirmation_fingerprint=pf['coverage']['confirmation_fingerprint'],decision='YES_PROCEED',node_id=718)
 result['test_only_partial_confirmation']=True
started=SC.start_run(db,request);assert started.get('ok'),started
run=started.get('run_id') or started['scenario']['run_id'];deadline=time.monotonic()+150
while time.monotonic()<deadline:
 sc=S.get_scenario(db,run)
 if sc['status'] in SC.TERMINAL:break
 time.sleep(.1)
assert sc['status'] in ('COMPLETED','PARTIAL'),sc
again=SC.start_run(db,request);assert again['scenario']['run_id']==run and again['engine_rerun'] is False
result['actual_historical_run']={k:sc.get(k) for k in ('run_id','node_id','status','bars_used','dataset_id','dataset_fingerprint','actual_start','actual_end','coverage_status','analytics','engine_versions')}
result['repeated_request_reused_same_id']=True
result['qualified_after_import_and_deep']=nodes_index(limit=0)['total']
result['exact_nodes']=[r for n in (718,726,728,735,737) for r in nodes_index(search=str(n),limit=0)['nodes'] if r['node_id']==n]
result['ranking_top']=[{'node_id':r['node_id'],'historical_statistics':r['historical_statistics']} for r in nodes_index(limit=0,sort_by='canonical_win_rate',sort_desc=True)['nodes'][:10]]
result['old_duplicate_signatures']=[dict(r) for r in db.q('SELECT node_id,signature,COUNT(*) n FROM deeptest_scenarios WHERE signature IS NOT NULL GROUP BY node_id,signature HAVING COUNT(*)>1')]
Path('/home/user/V9_3_ACTUAL_DATA_AUDIT.json').write_text(json.dumps(result,indent=2,default=str));print('AUDIT PASSED',flush=True)
# A completely fresh interpreter queries the normalized store without CSV loading.
code="""import json;from app.deeptest.coverage import _mstore;from app.data.engine import get_data_engine;from app.data.history import TIMEFRAMES;out={};\nfor tf in TIMEFRAMES:\n s=_mstore('XAUUSD',tf);did=get_data_engine()._materialize_dataset_view(s);out[tf]={'unique_rows':int(get_data_engine().get_frame(did).ts.nunique()),'fingerprint':s.fingerprint}\nprint(json.dumps(out))"""
p=subprocess.run([sys.executable,'-c',code],cwd=repo,env={**os.environ,'PYTHONPATH':str(repo/'backend')},capture_output=True,text=True,timeout=120)
assert p.returncode==0,p.stderr
fresh=json.loads(p.stdout.strip().splitlines()[-1]);Path('/home/user/V9_3_FRESH_BACKEND_QUERY.json').write_text(json.dumps(fresh,indent=2));assert sum(x['unique_rows'] for x in fresh.values())==sum(x['queryable_after'] for x in result['files']);print('FRESH PROCESS QUERY PASSED',fresh,flush=True)
