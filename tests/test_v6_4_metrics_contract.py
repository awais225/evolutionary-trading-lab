"""V6.4 #4 — every metric names ONE evaluation, every unit is stated.

The example node's numbers (net +670.82, return 6.71%, PF 2.91, maxDD 0.6%,
win 70.5%, 44 trades) must reconcile against an independently-computable trade
ledger, and every displayed metric must refer to the same node and the same
evaluation version. Units are explicit: return/maxDD/win_rate are FRACTIONS in
the payload (0.0671 = 6.71%), profit_factor is a RATIO (2.91 = winners made
2.91x losers — never 291%), net_profit is account currency, trades is a count.

The fixture ledger below is small enough to add up by hand: 10 trades, gross
profit 300, gross loss 100, net 200 on 10,000 initial capital.
"""
from __future__ import annotations

import json
import time

import pytest


# the independently-verifiable ledger (hand-checkable):
#   wins:  7 trades x +50  = +350  ... adjusted below to fixed round numbers
#   losses: 3 trades x -33.333.. = -100
#   net = +200 on 10,000 -> 2.00% ; PF = 350/100 = 3.5 ; win rate = 7/10 = 70%
LEDGER = [
    {"pnl": 50.0}, {"pnl": 50.0}, {"pnl": 50.0}, {"pnl": 50.0},
    {"pnl": 50.0}, {"pnl": 50.0}, {"pnl": 50.0},                  # 7 wins
    {"pnl": -33.333333333333336}, {"pnl": -33.333333333333336},
    {"pnl": -33.333333333333333},                                 # 3 losses
]
GROSS_PROFIT = 350.0
GROSS_LOSS = 100.0
NET_PROFIT = 250.0          # 350 - 100
INITIAL = 10000.0
FINAL = INITIAL + NET_PROFIT


def _metrics(stage="detail"):
    return {
        "stage": stage,
        "dataset_id": "TEST_M15",
        "window": [0, 100],
        "initial_capital": INITIAL,
        "final_equity": FINAL,
        "gross_profit": GROSS_PROFIT,
        "gross_loss": GROSS_LOSS,
        "net_profit": NET_PROFIT,
        "total_return_pct": NET_PROFIT / INITIAL,          # 0.025 = 2.5%
        "profit_factor": GROSS_PROFIT / GROSS_LOSS,        # 3.5 (a RATIO)
        "profit_factor_raw": GROSS_PROFIT / GROSS_LOSS,
        "max_drawdown_pct": 0.004,                         # 0.4%
        "win_rate": 0.7,                                   # 70%
        "trades": 10,
        "sharpe": 1.5,
        "trades_sample": [{**t, "entry_ts": 1.0, "exit_ts": 2.0} for t in LEDGER],
        "genome_hash": "f" * 32,
    }


@pytest.fixture()
def db(tmp_path):
    from app.db.database import Database
    d = Database(str(tmp_path / "metrics.db"))
    d.get_shortlist()            # materialise lazily-created tables
    d.x("DELETE FROM strategies")
    d.x("DELETE FROM backtests")
    d.x("DELETE FROM validations")
    now = time.time()
    d.x("""INSERT INTO strategies (id, hash, parent_id, generation, symbol, timeframe,
            direction, status, genome, complexity, fitness, created_at, updated_at,
            origin, run_id, data_source, research_node_num)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (4242, "metrics-4242", None, 3, "XAUUSD", "M15", "both", "QUALIFIED",
         json.dumps({"symbol": "XAUUSD", "timeframe": "M15",
                     "entry_long": {"type": "compare", "left": "close", "cmp": ">",
                                    "right": 0}}),
         3, 0.75, now, now, "research", "RUN-METRICS-TEST", "USER_RESEARCH", 17))
    # TWO evaluation rows: a screen one (older) and the detail one (the report's)
    d.x("""INSERT INTO backtests (strategy_id, stage, dataset_id, window, params,
            metrics, created_at, fitness, verdict, fingerprint, dataset_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (4242, "screen", "TEST_M15", json.dumps([0, 100]), "{}",
         json.dumps(_metrics("screen")), now - 100, 0.5, "SURVIVED", "fp-screen", "v1"))
    d.x("""INSERT INTO backtests (strategy_id, stage, dataset_id, window, params,
            metrics, created_at, fitness, verdict, fingerprint, dataset_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (4242, "detail", "TEST_M15", json.dumps([0, 100]), "{}",
         json.dumps(_metrics("detail")), now, 0.75, "SURVIVED", "fp-detail", "v1"))
    return d


@pytest.fixture()
def api(monkeypatch, db):
    import app.api.routes as routes
    import app.db.database as database_mod
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setattr(database_mod, "get_db", lambda: db)
    monkeypatch.setattr(routes, "get_db", lambda: db)
    return TestClient(app)


# --------------------------------------------------------------------------- #
# the ledger reconciles, independently
# --------------------------------------------------------------------------- #
def test_01_the_example_numbers_are_derivable_from_the_trade_ledger():
    """net = GP - GL; return = net / initial; PF = GP / GL; win = wins / trades."""
    gp = sum(t["pnl"] for t in LEDGER if t["pnl"] > 0)
    gl = -sum(t["pnl"] for t in LEDGER if t["pnl"] < 0)
    net = gp - gl
    wins = sum(1 for t in LEDGER if t["pnl"] > 0)
    assert gp == pytest.approx(GROSS_PROFIT)
    assert gl == pytest.approx(GROSS_LOSS)
    assert net == pytest.approx(NET_PROFIT)
    assert net / INITIAL == pytest.approx(0.025)                 # 2.5% of capital
    assert gp / gl == pytest.approx(3.5)                         # PF 3.5, NOT 350%
    assert wins / len(LEDGER) == pytest.approx(0.7)


def test_02_stored_metrics_reconcile_with_the_ledger(db):
    m = json.loads(db.one("SELECT metrics FROM backtests WHERE strategy_id=4242 AND stage='detail'")["metrics"])
    assert m["net_profit"] == pytest.approx(250.0)
    assert m["total_return_pct"] == pytest.approx(m["net_profit"] / m["initial_capital"])
    assert m["final_equity"] == pytest.approx(m["initial_capital"] + m["net_profit"])
    assert m["profit_factor"] == pytest.approx(m["gross_profit"] / m["gross_loss"])
    assert m["win_rate"] == pytest.approx(7 / 10)
    assert m["trades"] == len(LEDGER)
    # units: fractions and ratios, never mixed
    assert 0.0 < m["total_return_pct"] < 1.0          # FRACTION (2.5%, not 2.5 or 250)
    assert m["profit_factor"] == pytest.approx(3.5)   # RATIO (not 350%)


# --------------------------------------------------------------------------- #
# the authoritative payload: one evaluation, stated units
# --------------------------------------------------------------------------- #
def test_03_economics_metrics_all_carry_the_same_evaluation_id(api):
    d = api.get("/api/strategies/4242/economics").json()
    ev = d["evaluation"]
    # ONE evaluation version for every backtest metric on the page
    assert ev["stage"] == "detail"
    assert ev["backtest_id"] is not None
    assert ev["fingerprint"] == "fp-detail"
    assert ev["dataset_id"] == "TEST_M15"
    assert ev["window"] == "[0, 100]" or json.loads(ev["window"]) == [0, 100]
    assert d["metrics"]["backtest"]["stage"] == "detail"
    assert d["metrics"]["backtest"]["genome_hash"] == _metrics()["genome_hash"]
    # the return denominator is stated and derivable
    assert ev["initial_capital"] == pytest.approx(10000.0)
    assert ev["final_equity"] == pytest.approx(d["metrics"]["backtest"]["final_equity"])


def test_04_evaluation_block_states_every_definition_and_unit(api):
    d = api.get("/api/strategies/4242/economics").json()
    ev = d["evaluation"]
    assert "FRACTION" in ev["return_definition"] and "net_profit / initial_capital" in ev["return_definition"]
    assert "RATIO" in ev["profit_factor_definition"] and "never 291%" in ev["profit_factor_definition"]
    assert "FRACTION" in ev["max_drawdown_definition"]
    assert "FRACTION" in ev["win_rate_definition"]
    assert "COUNT" in ev["trades_definition"]
    assert "ACCOUNT CURRENCY" in ev["net_profit_definition"]


def test_05_returns_block_is_consistently_fraction_based(api):
    d = api.get("/api/strategies/4242/economics").json()
    assert d["returns"]["backtest_return_pct"] == pytest.approx(0.025)
    # the SAME value the metrics carry (no second computation, no unit drift)
    assert d["returns"]["backtest_return_pct"] == pytest.approx(
        d["metrics"]["backtest"]["total_return_pct"])
    assert d["profit_factors"]["in_sample"] == pytest.approx(3.5)


def test_06_node_rows_carry_the_same_evaluation_metrics_as_the_detail(api):
    rows = api.get("/api/nodes", params={"filter": "qualified", "limit": 10}).json()["nodes"]
    row = next(r for r in rows if r["node_id"] == 4242)
    m = api.get("/api/strategies/4242/economics").json()["metrics"]["backtest"]
    assert row["metrics"]["return_pct"] == pytest.approx(m["total_return_pct"])
    assert row["metrics"]["profit_factor"] == pytest.approx(m["profit_factor"])
    assert row["metrics"]["net_profit"] == pytest.approx(m["net_profit"])
    assert row["metrics"]["trades"] == m["trades"]


def test_07_real_example_node_reconciles_when_the_lab_data_is_available():
    """The handoff example (net +670.82, return 6.71%, PF 2.91, 44 trades) must
    reconcile as net/initial and GP/GL — checked against the real lab DB when
    the DATA tree is mounted (read-only), skipped otherwise."""
    import os
    import sqlite3
    from pathlib import Path
    root = os.environ.get("EVOLUTIONARY_LAB_DATA_ROOT", "DATA")
    dbp = Path(root) / "DATABASE" / "lab_state.db"
    if not dbp.exists():
        pytest.skip("lab DATA tree not mounted")
    con = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT metrics FROM backtests WHERE strategy_id=6658 AND stage='detail'").fetchone()
    finally:
        con.close()
    if not row:
        pytest.skip("example node not in this DATA tree")
    m = json.loads(row[0])
    # the example values are internally consistent under the stated definitions
    assert m["net_profit"] == pytest.approx(670.82, abs=0.01)
    assert m["total_return_pct"] == pytest.approx(m["net_profit"] / 10000.0, abs=0.0002)
    assert m["profit_factor"] == pytest.approx(
        m["gross_profit"] / m["gross_loss"], rel=0.01)
    assert m["trades"] == 44
    assert 0.0 < m["total_return_pct"] < 1.0          # 6.71% as the fraction 0.0671
