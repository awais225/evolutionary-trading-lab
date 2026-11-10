import json
from pathlib import Path

import pytest
from hero1_storage.init import initialize_databases
from hero1_storage.engine import instrument_session, registry_session
from hero1_storage.instrument_models import Node, DeepTestRun, DeepTestRunTrade, MonteCarloRun, DeepTestScenarioDef
from hero1_storage.registry_models import ApplicationSetting, GlobalEvent
from app import data_management_service as dm


def _root(tmp_path,monkeypatch):
    root=tmp_path/'managed-data';monkeypatch.setenv('HERO1_DATA_ROOT',str(root))
    initialize_databases(root,create=True,instruments=('XAUUSD',))
    return root


def _seed_pair():
    db=instrument_session('XAUUSD')
    node=Node(instrument_id='XAUUSD',origin='CUSTOM',name='Disposable node',status='DRAFT',approval_state='NONE',version=1)
    db.add(node);db.flush()
    run=DeepTestRun(instrument_id='XAUUSD',node_id=node.id,node_version=1,mode='initial',status='DONE',config_json='{}',metrics_json='{}',started_at=1,finished_at=2)
    db.add(run);db.flush()
    db.add(DeepTestRunTrade(instrument_id='XAUUSD',run_id=run.id,node_id=node.id,side='BUY',volume=0.1,entry_ts=1,entry_price=1,exit_ts=2,exit_price=2,detail_json='{}',net_pnl=1))
    study=MonteCarloRun(id='study-disposable',instrument_id='XAUUSD',node_id=node.id,deeptest_run_id=run.id,method='RESHUFFLE',seed=7,status='DONE',config_json=json.dumps({'source_run_id':run.id}),result_json='{}')
    db.add(study);db.commit();ids=(node.id,run.id);db.close();return ids


def test_deeptesting_delete_detaches_and_preserves_monte_carlo(tmp_path,monkeypatch):
    _root(tmp_path,monkeypatch);node_id,run_id=_seed_pair()
    result=dm.execute('DEEPTESTING');assert result['success']
    db=instrument_session('XAUUSD')
    assert db.query(DeepTestRun).count()==0 and db.query(DeepTestRunTrade).count()==0
    study=db.get(MonteCarloRun,'study-disposable')
    assert study and study.status=='DONE' and study.deeptest_run_id is None
    assert json.loads(study.config_json)['source_run_id_snapshot']==run_id
    assert db.get(Node,node_id) is not None
    db.close()


def test_market_and_log_deletion_is_scoped_and_disposable(tmp_path,monkeypatch):
    root=_root(tmp_path,monkeypatch)
    market=root/'MARKET_DATA';(market/'IMPORTS'/'XAUUSD').mkdir(parents=True)
    (market/'market_data.db').write_text('disposable db');(market/'import_manifest.json').write_text('{}')
    (market/'IMPORTS'/'XAUUSD'/'sample.csv').write_text('ts,open\n1,1\n')
    metadata=root/'MARKET_METADATA'/'broker';metadata.mkdir(parents=True);meta=metadata/'XAUUSD.json';meta.write_text('{}')
    logs=root/'LOGS';logs.mkdir(exist_ok=True);(logs/'startup.log').write_text('disposable log')
    reg=registry_session();reg.add(GlobalEvent(ts=1,category='SYSTEM',severity='INFO',subsystem='test',message='disposable event'));reg.commit();reg.close()
    assert dm.execute('MARKET_DATA')['success']
    assert not market.exists() and meta.exists()
    assert dm.execute('LOGS')['success'] and not logs.exists()
    reg=registry_session();assert reg.query(GlobalEvent).count()==0;reg.close()


def test_node_data_delete_removes_dependent_test_records(tmp_path,monkeypatch):
    _root(tmp_path,monkeypatch);node_id,_=_seed_pair()
    result=dm.execute('NODES');assert result['success']
    db=instrument_session('XAUUSD')
    assert db.query(Node).count()==0 and db.query(DeepTestRun).count()==0 and db.query(MonteCarloRun).count()==0
    db.close()
    reg=registry_session();assert reg.get(ApplicationSetting,'startup.seed_nodes_disabled').value=='true';reg.close()


def test_all_data_scope_preserves_metadata_credentials_and_external_files(tmp_path,monkeypatch):
    root=_root(tmp_path,monkeypatch);_seed_pair()
    metadata=root/'MARKET_METADATA';metadata.mkdir();(metadata/'snapshot.json').write_text('{}')
    credential=root/'credentials.txt';credential.write_text('retain')
    external=tmp_path/'source.py';external.write_text('source')
    (root/'MARKET_DATA'/'IMPORTS'/'XAUUSD').mkdir(parents=True)
    (root/'MARKET_DATA'/'IMPORTS'/'XAUUSD'/'source.csv').write_text('data')
    assert dm.execute('ALL')['success']
    assert metadata.exists() and credential.read_text()=='retain' and external.read_text()=='source'
    db=instrument_session('XAUUSD');assert db.query(Node).count()==0 and db.query(DeepTestRun).count()==0;db.close()
    reg=registry_session()
    assert reg.get(ApplicationSetting,'startup.seed_nodes_disabled').value=='true'
    assert reg.get(ApplicationSetting,'startup.market_baseline_disabled').value=='true'
    reg.close()
