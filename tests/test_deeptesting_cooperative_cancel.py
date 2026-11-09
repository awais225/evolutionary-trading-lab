from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
ROOT = Path(__file__).resolve().parents[1]
for p in ("packages/strategy_spec/src", "packages/exec_semantics/src", "packages/indicators/src"):
    sys.path.insert(0, str(ROOT / p))
from hero1_exec_semantics import EngineError, run_backtest
from hero1_strategy_spec import compile_spec
from test_phase3_engine import CFG, CTX, SPEC_INSTR, candles_from_closes, cross_spec
from app.task_service import task_dict


def test_cancel_check_stops_engine_before_processing_entire_candle_series():
    ir = compile_spec(cross_spec())
    seen = []
    def progress(done, total, trades):
        seen.append((done, total, trades))
    with pytest.raises(EngineError, match="cancellation requested") as exc:
        run_backtest(ir=ir.ir, candles=candles_from_closes(list(range(100, 0, -1))),
                     cfg=CFG, instrument=SPEC_INSTR, price_ctx=CTX,
                     cancel_check=lambda: True, progress_cb=progress)
    assert exc.value.code == "CANCELLED"
    assert seen == []


def test_global_task_percentage_includes_active_authoritative_progress_but_never_early_100():
    row = SimpleNamespace(id='t', category='DEEPTESTING', instrument_id='XAUUSD', label='test',
        status='RUNNING', total_units=1, completed_units=0, current_unit='Node 1',
        current_unit_progress=64.0, summary=None, error=None, acknowledged=False,
        payload_json='{}', created_at=1, started_at=1, updated_at=1, finished_at=None)
    assert task_dict(row)['percentage'] == 64.0
    row.current_unit_progress = 100.0
    assert task_dict(row)['percentage'] == 99.0
    row.status = 'COMPLETED'
    assert task_dict(row)['percentage'] == 100.0
