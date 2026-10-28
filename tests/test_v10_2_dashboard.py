"""Dashboard contracts only; always execute from a TEMP tree and isolated DATA."""
from types import SimpleNamespace
import pytest


@pytest.mark.parametrize('width',[0,65,-1,True,False,'2',None,1.2,float('inf'),float('nan')])
def test_invalid_process_setting_rejected_before_mutation(width,monkeypatch):
    from app import config as C
    saved=[]
    monkeypatch.setattr(C,'save_config',lambda *a:saved.append(a))
    cfg=C.get_config();before=cfg.resources.deep_test_workers
    with pytest.raises(ValueError,match='integer 1..64'):
        C.update_config('resources',{'deep_test_workers':width})
    assert cfg.resources.deep_test_workers==before
    assert not saved


@pytest.mark.parametrize('width',[1,2,64])
def test_valid_optin_width_contract(width):
    from app.config import validate_resource_patch
    validate_resource_patch({'deep_test_workers':width})


def test_apply_invalid_width_does_not_partially_apply_cpu_target():
    from app.config import get_config
    from app.api.routes import post_apply_settings
    from fastapi import HTTPException
    before=get_config().resources.cpu_target_pct
    with pytest.raises(HTTPException) as err:
        post_apply_settings({'cpu_target_pct':100,'resources':{'deep_test_workers':0}})
    assert err.value.status_code==422
    assert get_config().resources.cpu_target_pct==before


def test_process_samples_retain_child_baseline_and_reuse_short_interval(monkeypatch):
    from app.resources import manager as M
    class Child:
        pid=99999
        def __init__(self):self.values=iter([0,37])
        def create_time(self):return 1
        def cpu_percent(self,interval=None):return next(self.values)
        def memory_info(self):return SimpleNamespace(rss=1024**2)
    class Parent:
        values=iter([0,23])
        def cpu_percent(self,interval=None):return next(self.values)
        def memory_info(self):return SimpleNamespace(rss=2*1024**2)
        def children(self,recursive=True):return [Child()]
    rm=M.ResourceManager();rm._proc=Parent()
    clock=iter([100,100.1,101]);monkeypatch.setattr(M.time,'monotonic',lambda:next(clock))
    assert rm._inspect_process_metrics()==(None,3.0)
    assert rm._inspect_process_metrics()==(None,3.0)
    assert rm._inspect_process_metrics()==(60.0,3.0)


def test_failed_process_probe_is_unavailable_not_zero():
    from app.resources.manager import ResourceManager
    rm=ResourceManager();rm._proc=object()
    assert rm._inspect_process_metrics()==(None,None)


def test_status_row_device_gpu_activity_is_not_application_compute(client,monkeypatch):
    from app.resources.manager import get_resource_manager
    rm=get_resource_manager();live=rm.live_metrics
    def measured():
        res=live()
        res['gpu'].update(enabled=True,available=True,utilization_pct=90,application_gpu_compute_verified=False)
        return res
    monkeypatch.setattr(rm,'live_metrics',measured)
    response=client.get('/api/system/status_row')
    assert response.status_code==200,response.text
    gpu=response.json()['gpu']
    assert gpu['utilization_pct']==90
    assert gpu['processing_state']=='NOT VERIFIED'
    assert gpu['application_gpu_compute_verified'] is False
    assert gpu['historical_compute_backend']=='NumPy/pandas CPU'
    assert 'not application CUDA' in gpu['telemetry_scope']


def test_deep_capacity_snapshot_is_read_only_not_active_worker_estimate(monkeypatch):
    import copy
    from app import config as C
    from app.deeptest import parallel as P
    cfg=copy.deepcopy(C.get_config());cfg.resources.deep_test_workers=2
    monkeypatch.setattr(C,'_config',cfg)
    before=P._PROCESS_POOL
    snapshot=P.telemetry_snapshot()
    assert snapshot['configured_processes']==2
    assert snapshot['effective_process_limit']>=1
    assert snapshot['admission_capacity']>=2
    assert snapshot['observed_active_processes'] is None
    assert P._PROCESS_POOL is before
    assert 'queued' in snapshot['scope']
