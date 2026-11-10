def test_refresh_process_status_and_stop_all_are_backend_actions(client):
    before=client.get('/api/v1/system/processes')
    assert before.status_code==200
    snapshot=before.json()
    assert {'worker_count','queued_jobs','running_jobs','counts','active_jobs','resources'} <= snapshot.keys()
    stopped=client.post('/api/v1/system/processes/stop',json={'timeout_seconds':2})
    assert stopped.status_code==200,stopped.text
    state=stopped.json()
    assert state['stopped'] is True and state['worker_count']==0
    assert state['accepting_new_jobs'] is True
    assert 'No operating-system processes were killed' not in state.get('message','')
    assert client.get('/api/v1/system/processes').json()['worker_count']==0


def test_destructive_delete_requires_exact_phrase_and_previews_counts(client):
    preview=client.post('/api/v1/system/data/preview',json={'category':'NODES'})
    assert preview.status_code==200,preview.text
    data=preview.json()
    assert data['category']=='NODES' and data['can_delete'] is True,data
    assert 'nodes' in data['instrument_records']['XAUUSD']
    assert data['included'] and data['data_root']
    rejected=client.post('/api/v1/system/data/delete',json={'category':'NODES','confirm':'yes'})
    assert rejected.status_code==422
    assert client.get('/api/v1/instruments/XAUUSD/nodes').status_code==200


def test_shutdown_requires_explicit_backend_confirmation(client):
    bad=client.post('/api/v1/system/shutdown',json={'confirm':'','timeout_seconds':1})
    assert bad.status_code==422
    # Deliberately do not invoke a process-killing endpoint inside the pytest runner.
