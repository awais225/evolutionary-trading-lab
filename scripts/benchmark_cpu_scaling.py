"""
V3.5 Phase 2 — CPU Utilization & Worker Pool Scaling Benchmark.

Measures backtesting throughput, worker scaling, and telemetry across
25%, 50%, 75%, and 100% CPU target allocations.
Outputs results to console and logs to DATA/logs/CPU_BENCHMARK_V3_5.log.
"""
import json
import logging
import os
import sys
import time
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from backend.app import paths as P
from backend.app.config import get_config
from backend.app.orchestrator.lab import get_lab
from backend.app.resources.manager import get_resource_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("benchmark")


def generate_benchmark_payloads(count: int = 40):
    """Generate representative strategy backtest payloads."""
    payloads = []
    for i in range(count):
        payload = {
            "strategy_id": 90000 + i,
            "genome": {
                "symbol": "XAUUSD",
                "timeframe": "M15",
                "indicators": [
                    {"name": "ema", "period": 14 + (i % 20)},
                    {"name": "rsi", "period": 14, "lower": 30, "upper": 70},
                    {"name": "atr", "period": 14},
                ],
                "rules": {
                    "entry_long": "ema_14 > ema_cross AND rsi < 30",
                    "exit_long": "rsi > 70",
                },
                "parameters": {"tp_pips": 50, "sl_pips": 30},
            },
            "dataset_id": "XAUUSD_M15_synthetic",
            "window": [0, 500],
            "stage": "screen",
        }
        payloads.append(payload)
    return payloads


def run_benchmark():
    rm = get_resource_manager()
    lab = get_lab()
    cores = os.cpu_count() or 2

    log.info("Starting V3.5 Phase 2 Benchmark on %d logical cores...", cores)
    benchmark_log = P.DATA_LOGS_DIR / "CPU_BENCHMARK_V3_5.log"
    P.DATA_LOGS_DIR.mkdir(parents=True, exist_ok=True)

    allocations = [25, 50, 75, 100]
    results = []

    for alloc in allocations:
        log.info("--- Testing CPU Target Allocation: %d%% ---", alloc)
        # 1. Dynamically set target
        rep = rm.set_cpu_target(alloc)
        eff_workers = rep["effective_workers"]

        # Ensure lab worker pool is sized to match
        lab.resize_pool()

        # 2. Warm up metrics
        metrics_before = rm.live_metrics()

        # 3. Execute batch
        payload_count = max(20, eff_workers * 10)
        payloads = generate_benchmark_payloads(payload_count)

        t0 = time.perf_counter()
        batch_res = lab._run_batch(payloads)
        elapsed = time.perf_counter() - t0

        metrics_after = rm.live_metrics()
        throughput_sec = round(len(batch_res) / max(0.001, elapsed), 2)
        throughput_min = round(throughput_sec * 60, 1)

        bench_item = {
            "target_pct": alloc,
            "effective_workers": eff_workers,
            "payload_count": payload_count,
            "completed_count": len(batch_res),
            "elapsed_seconds": round(elapsed, 3),
            "tasks_per_second": throughput_sec,
            "tasks_per_minute": throughput_min,
            "avg_latency_ms": metrics_after["tasks"]["avg_duration_ms"],
            "system_cpu_pct": metrics_after["cpu"]["percent"],
            "app_cpu_pct": metrics_after["cpu"]["app_percent"],
            "per_core_cpu": metrics_after["cpu"]["per_core"],
            "system_ram_used_mb": metrics_after["memory"]["used_mb"],
            "system_ram_avail_mb": metrics_after["memory"]["available_mb"],
            "app_ram_rss_mb": metrics_after["memory"]["app_used_mb"],
            "app_peak_ram_mb": metrics_after["memory"]["app_peak_mb"],
        }
        results.append(bench_item)

        log.info(
            "Allocation %d%% -> %d workers | Processed %d tasks in %.3fs (%.1f tasks/min) | Sys CPU: %.1f%% | App CPU: %.1f%% | App RAM: %.1fMB",
            alloc, eff_workers, len(batch_res), elapsed, throughput_min,
            bench_item["system_cpu_pct"], bench_item["app_cpu_pct"], bench_item["app_ram_rss_mb"]
        )

        # Append to log file
        with open(benchmark_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "benchmark": bench_item
            }) + "\n")

    # Restore default target (60%)
    rm.set_cpu_target(60)
    lab.resize_pool()

    print("\n" + "=" * 80)
    print("V3.5 PHASE 2 CPU ALLOCATION & WORKER SCALING BENCHMARK RESULTS")
    print("=" * 80)
    print(f"{'Target %':<10} {'Workers':<10} {'Tasks':<10} {'Elapsed (s)':<14} {'Tasks/min':<14} {'App CPU %':<12} {'App RAM (MB)':<12}")
    print("-" * 80)
    for r in results:
        print(f"{r['target_pct']:<10} {r['effective_workers']:<10} {r['payload_count']:<10} {r['elapsed_seconds']:<14.3f} {r['tasks_per_minute']:<14.1f} {r['app_cpu_pct']:<12.1f} {r['app_ram_rss_mb']:<12.1f}")
    print("=" * 80)
    print(f"Log written to: {benchmark_log.relative_to(ROOT_DIR)}\n")
    return results


if __name__ == "__main__":
    run_benchmark()
