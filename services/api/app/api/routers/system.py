"""Read-only resource metrics for the host running the HERO1 API."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/metrics")
def system_metrics():
    """Return host metrics; GPU absence is explicit, never represented as 0%."""
    try:
        import psutil
        vm = psutil.virtual_memory()
        cpu = {
            "utilization_pct": psutil.cpu_percent(interval=None),
            "logical_threads": psutil.cpu_count(logical=True),
            "physical_cores": psutil.cpu_count(logical=False),
            "load_average": list(psutil.getloadavg()) if hasattr(psutil, "getloadavg") else None,
            "ram_used_bytes": int(vm.used), "ram_total_bytes": int(vm.total),
            "ram_utilization_pct": float(vm.percent),
        }
    except Exception as exc:  # metrics must never make the app unavailable
        cpu = {"available": False, "reason": f"CPU/RAM metrics unavailable: {type(exc).__name__}"}
    gpu = {"available": False, "reason": "No supported GPU monitoring interface is available in this API environment"}
    try:
        import pynvml  # optional dependency, not required for application startup
        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode(errors="replace")
            try:
                temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            except Exception:
                temp = None
            gpu = {"available": True, "name": name, "utilization_pct": int(util.gpu),
                   "vram_used_bytes": int(mem.used), "vram_total_bytes": int(mem.total),
                   "temperature_c": temp}
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        pass
    return {"scope": "API host / LMS sandbox", "cpu_ram": cpu, "gpu": gpu}
