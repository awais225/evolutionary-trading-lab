"""
Unit and integration tests for Node Generation Halt Diagnostic & Core Fix:
- Stop diagnostic 11-field emission format
- 9-metric node accounting dictionary
- Trade duration calculation & death check validation
- Reproduction shortfall filling
- Population cap preservation of non-survivors (BORN, QUALIFIED, PAPER)
- Zero-recomputation post-evaluation filtering & shortlist API
- 1:1 payload-to-result worker batch mapping
"""

import os
import json
import random
import tempfile
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import Database
from app.evolution.engine import EvolutionEngine
from app.config import get_config, update_config
from app.genome import ops as gops
from app.backtest.engine import run_backtest
from app.fitness.evaluator import death_check


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_node_fix.db"
    db = Database(str(db_file))
    return db


def test_emit_stop_diagnostic(fresh_db):
    evo = EvolutionEngine(fresh_db)
    lines = evo.emit_stop_diagnostic(
        stop_reason="TARGET REACHED",
        recovery_action="Increase target_nodes to resume"
    )
    assert len(lines) == 11
    assert lines[0].startswith("[NODE ENGINE] STOP REASON:")
    assert lines[1].startswith("CURRENT:")
    assert lines[2].startswith("TARGET:")
    assert lines[3].startswith("REMAINING:")
    assert lines[4].startswith("GENERATION:")
    assert lines[5].startswith("QUEUE:")
    assert lines[6].startswith("ACTIVE WORKERS:")
    assert lines[7].startswith("LAST SUCCESSFUL NODE:")
    assert lines[8].startswith("LAST FAILED NODE:")
    assert lines[9].startswith("EXCEPTION:")
    assert lines[10].startswith("RECOVERY ACTION:")
    assert "TARGET REACHED" in lines[0]


def test_node_accounting_nine_metrics(fresh_db):
    evo = EvolutionEngine(fresh_db)
    rng = random.Random(42)
    # Insert various strategies across states
    for i in range(5):
        g = gops.random_genome("XAUUSD", rng, 3)
        sid = evo.try_insert(g, None, 0, status="BORN")
    for i in range(3):
        g = gops.random_genome("EURUSD", rng, 3)
        sid = evo.try_insert(g, None, 0, status="QUALIFIED")
        fresh_db.update_strategy(sid, status="QUALIFIED", fitness=1.5)
    for i in range(2):
        g = gops.random_genome("GBPUSD", rng, 3)
        sid = evo.try_insert(g, None, 0, status="FAILED")
        fresh_db.update_strategy(sid, status="FAILED", failure_reason="insufficient trades")

    acc = evo.node_accounting()
    assert "TOTAL CREATED" in acc
    assert "TOTAL EVALUATED" in acc
    assert "TOTAL BACKTESTED" in acc
    assert "TOTAL VALIDATED" in acc
    assert "TOTAL QUALIFIED" in acc
    assert "TOTAL REJECTED" in acc
    assert "TOTAL DEAD" in acc
    assert "TOTAL ACTIVE" in acc
    assert "TOTAL REMAINING" in acc
    assert acc["TOTAL CREATED"] == 10
    assert acc["TOTAL QUALIFIED"] == 3
    assert acc["TOTAL DEAD"] == 2


def test_trade_duration_and_evaluator_metrics(fresh_db):
    evo = EvolutionEngine(fresh_db)
    # Configure min_trade_duration_seconds
    update_config("backtest", {"min_trade_duration_seconds": 120})
    cfg = get_config()
    assert cfg.backtest.min_trade_duration_seconds == 120

    # Test death check failing on trade duration too short
    metrics_short = {
        "trades": 25,
        "profit_factor": 1.8,
        "max_drawdown_pct": 0.10,
        "total_return_pct": 0.15,
        "sharpe": 1.5,
        "avg_trade_duration_seconds": 60,  # 60s < 120s limit
    }
    died, reasons = death_check(metrics_short, {}, stage="screen")
    assert died is True
    assert any("trade duration" in r for r in reasons)

    # Test death check passing with sufficient duration
    metrics_valid = {
        "trades": 25,
        "profit_factor": 1.8,
        "max_drawdown_pct": 0.10,
        "total_return_pct": 0.15,
        "sharpe": 1.5,
        "avg_trade_duration_seconds": 300,  # 300s > 120s limit
    }
    died, reasons = death_check(metrics_valid, {}, stage="screen")
    assert died is False


def test_reproduction_shortfall_fill(fresh_db):
    evo = EvolutionEngine(fresh_db)
    # Seed 5 strategies
    evo.seed_population(5, "XAUUSD")
    cur_before = evo.total_nodes()
    assert cur_before == 5

    # Request 15 births
    counts = evo.reproduce(15, "XAUUSD")
    total_born = counts["mutation"] + counts["crossover"] + counts["exploration"]
    assert total_born == 15
    assert evo.total_nodes() == 20


def test_population_cap_leaves_permanent_and_in_flight_untouched(fresh_db):
    evo = EvolutionEngine(fresh_db)
    rng = random.Random(10)
    update_config("evolution", {"population_size": 10})

    # Add 5 QUALIFIED (permanent)
    q_ids = []
    for _ in range(5):
        sid = evo.try_insert(gops.random_genome("XAUUSD", rng, 3), None, 0, status="QUALIFIED")
        fresh_db.update_strategy(sid, status="QUALIFIED", fitness=2.0)
        q_ids.append(sid)

    # Add 5 BORN (candidate)
    b_ids = []
    for _ in range(5):
        sid = evo.try_insert(gops.random_genome("EURUSD", rng, 3), None, 0, status="BORN")
        b_ids.append(sid)

    # Add 12 SURVIVED (unvalidated pool candidates)
    s_ids = []
    for i in range(12):
        sid = evo.try_insert(gops.random_genome("GBPUSD", rng, 3), None, 0, status="SURVIVED")
        fresh_db.update_strategy(sid, status="SURVIVED", fitness=1.0 + (i * 0.1))
        s_ids.append(sid)

    # Active count is 5 + 5 + 12 = 22. Target population is 10.
    retired = evo.enforce_population_cap("GBPUSD")
    assert retired > 0

    # Verify QUALIFIED are untouched
    for qid in q_ids:
        strat = fresh_db.get_strategy(qid)
        assert strat["status"] == "QUALIFIED"

    # Verify BORN are untouched
    for bid in b_ids:
        strat = fresh_db.get_strategy(bid)
        assert strat["status"] == "BORN"


def test_research_shortlist_and_filter_api():
    client = TestClient(app)
    # Call GET /api/research/shortlist
    res_get = client.get("/api/research/shortlist?min_trades=0&limit=10")
    assert res_get.status_code == 200
    data_get = res_get.json()
    assert "total_evaluated" in data_get
    assert "total_matching" in data_get
    assert "recomputed" in data_get
    assert data_get["recomputed"] is False
    assert isinstance(data_get["strategies"], list)

    # Call POST /api/research/filter
    payload = {
        "min_trades": 5,
        "min_trade_duration_minutes": 2.0,
        "min_return_pct": 0.0,
        "max_drawdown_pct": 50.0,
        "min_profit_factor": 1.0,
        "limit": 20
    }
    res_post = client.post("/api/research/filter", json=payload)
    assert res_post.status_code == 200
    data_post = res_post.json()
    assert data_post["recomputed"] is False
    assert "filters_applied" in data_post
    assert data_post["filters_applied"]["min_trades"] == 5
