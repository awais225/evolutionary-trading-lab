"""BUY/SELL statistics, Inspect classification, trade pagination, TILL DATA
(Parts 2, 5, 7, 8, 10, 16A/B/G/H)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("packages/strategy_spec/src", "packages/exec_semantics/src", "packages/indicators/src"):
    sys.path.insert(0, str(ROOT / p))
sys.path.insert(0, str(ROOT / "services/api"))

from hero1_exec_semantics.analytics import compute_metrics, directional_stats  # noqa: E402


def tr(ts, side, pnl):
    return {"net_pnl": pnl, "exit_ts": ts, "entry_ts": ts - 60, "side": side,
            "volume": 1.0, "holding_seconds": 60}


EQ = [{"ts": 100, "equity": 1000.0, "balance": 1000.0, "open_positions": 0},
      {"ts": 900, "equity": 1000.0, "balance": 1000.0, "open_positions": 0}]


def test_directional_reconciliation_and_independent_drawdown():
    trades = [tr(100, "LONG", 100), tr(200, "SHORT", -50), tr(300, "LONG", -30),
              tr(400, "SHORT", 20), tr(500, "LONG", 0), tr(600, "SHORT", -10)]
    m = compute_metrics(trades, EQ, 1000.0)
    d = m["directional"]
    assert d["LONG"]["total_trades"] + d["SHORT"]["total_trades"] == m["total_trades"] == 6
    assert d["LONG"]["winning_trades"] + d["SHORT"]["winning_trades"] == m["winning_trades"]
    assert d["LONG"]["net_profit"] + d["SHORT"]["net_profit"] == m["net_profit"] == 30.0
    assert d["LONG"]["gross_profit"] + d["SHORT"]["gross_profit"] == m["gross_profit"]
    assert d["LONG"]["breakeven_trades"] == 1
    # independent LONG path: 1000 -> 1100 -> 1070 -> 1070: DD 30 vs peak 1100
    assert d["LONG"]["drawdown"]["max_amount"] == 30.0
    assert abs(d["LONG"]["drawdown"]["max_pct"] - 30 / 1100 * 100) < 1e-3
    # SHORT path: 1000 -> 950 -> 970 -> 960: max DD 50 (5%), current 40
    assert d["SHORT"]["drawdown"]["max_amount"] == 50.0
    assert d["SHORT"]["drawdown"]["current_amount"] == 40.0
    assert d["SHORT"]["profit_factor"] == round(20 / 60, 6)


def test_directional_empty_direction_is_zero_not_invented():
    d = directional_stats([tr(100, "LONG", 5)], 1000.0)
    assert d["SHORT"]["total_trades"] == 0
    assert d["SHORT"]["win_rate"] is None and d["SHORT"]["profit_factor"] is None
    assert d["SHORT"]["drawdown"]["max_amount"] == 0.0


def test_inspect_classification_weekend_unassigned():
    from app.deeptesting_service import inspect_cell
    # Sunday 2026-01-04 23:00 UTC, Asia hours -> UNASSIGNED weekday, ASIA window
    from datetime import datetime, timezone
    ts = int(datetime(2026, 1, 4, 23, tzinfo=timezone.utc).timestamp())
    assert inspect_cell(ts) == ("UNASSIGNED", "ASIA")
    ts_mon = int(datetime(2026, 1, 5, 8, tzinfo=timezone.utc).timestamp())
    assert inspect_cell(ts_mon) == ("Monday", "ASIA-LONDON")
    ts_sat = int(datetime(2026, 1, 3, 14, tzinfo=timezone.utc).timestamp())
    assert inspect_cell(ts_sat) == ("UNASSIGNED", "LONDON-NY")


def test_paginate_trades_server_side():
    from app.deeptesting_service import paginate_trades
    trades = [{"id": i, "entry_ts": 1767225600 + i * 3600, "exit_ts": 1767225600 + i * 3600,
               "net_pnl": float(i - 30), "side": "LONG", "volume": 1,
               "entry_price": 100 + i, "exit_price": 100, "initial_stop": 90,
               "take_profit": 110, "result": "WIN", "holding_seconds": 60,
               "exit_reason": "TAKE_PROFIT", "gross_pnl": float(i - 30)} for i in range(60)]
    out = paginate_trades(trades, page=0, page_size=25, sort="entry_ts", direction="asc")
    assert out["count"] == 60 and out["pages"] == 3 and len(out["trades"]) == 25
    # page size capped at 25
    assert paginate_trades(trades, page=0, page_size=100)["page_size"] == 25
    # numeric sort desc across the FULL set, stable by id
    out = paginate_trades(trades, page=0, page_size=25, sort="net_pnl", direction="desc")
    assert out["trades"][0]["net_pnl"] == 29.0 and out["trades"][0]["id"] == 59
    # cell filter: Monday 2026-01-05... trades start Thu 2026-01-01 00:00 UTC
    out = paginate_trades(trades, page=0, page_size=25, cells=["Thursday|ASIA"])
    assert 0 < out["count"] <= 24 and all(
        t["weekday"] == "Thursday" and t["session"] == "ASIA" for t in out["trades"])
