from app.api.routers.system import system_metrics


def test_system_metrics_reports_real_cpu_ram_and_explicit_gpu_state():
    out = system_metrics()
    assert out['scope'] == 'API host / LMS sandbox'
    cpu = out['cpu_ram']
    assert 'utilization_pct' in cpu or cpu.get('available') is False
    if cpu.get('available') is not False:
        assert 0 <= cpu['utilization_pct'] <= 100
        assert cpu['ram_total_bytes'] > 0
        assert 0 <= cpu['ram_utilization_pct'] <= 100
    gpu = out['gpu']
    assert isinstance(gpu['available'], bool)
    if not gpu['available']:
        assert gpu['reason']
        assert 'utilization_pct' not in gpu
