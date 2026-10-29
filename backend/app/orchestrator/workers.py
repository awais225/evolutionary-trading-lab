"""Process-pool worker entrypoints for parallel backtesting.

Workers must NEVER write to the shared feature-cache parquet files (the main
process owns persistence), so LAB_NO_FEATURE_PERSIST is set in the pool
initializer.
"""
from __future__ import annotations

from ..speed import profiled, worker_task
import os
from typing import Any, Dict


def init_worker() -> None:
    os.environ["LAB_NO_FEATURE_PERSIST"] = "1"
    import logging
    logging.getLogger("features.engine").setLevel(logging.WARNING)


@worker_task
@profiled('worker.backtest', hot=False)
def bt_worker(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run one backtest in a worker process. Payload mirrors BacktestRequest."""
    try:
        try:
            from ..backtest.engine import BacktestRequest, run_backtest
        except (ImportError, ValueError):
            from app.backtest.engine import BacktestRequest, run_backtest
        payload=dict(payload)
        window_context=payload.pop("_generation_window",None)
        req = BacktestRequest(**payload)
        res = run_backtest(req)
        extra={}
        if window_context:
            try:
                from ..research.generation_window import run
                extra['generation_window']=run(req.genome,window_context)
            except Exception as exc:
                extra['generation_window']={**window_context,'status':'FAILED','reason':type(exc).__name__,'metrics':None}
        return {
            **extra,
            "ok": res.ok,
            "error": res.error,
            "metrics": res.metrics,
            "trades": res.trades,
            "equity_curve": res.equity_curve,
        }
    except Exception as e:  # pragma: no cover
        import traceback
        return {"ok": False, "error": f"{e}\n{traceback.format_exc()[-800:]}",
                "metrics": {}, "trades": [], "equity_curve": []}


def feature_worker(frame, spec):
    from ..features.library import parse_spec
    fn,args=parse_spec(spec)
    return fn(frame,*args)
