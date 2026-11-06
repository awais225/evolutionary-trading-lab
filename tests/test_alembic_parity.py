"""Migrated schema must expose the expected tables (baseline parity check)."""
from hero1_storage.engine import get_engine, instrument_url, registry_url
from sqlalchemy import inspect, text


def test_registry_tables(data_root):
    eng = get_engine(registry_url(data_root))
    with eng.connect() as conn:
        tables = set(inspect(conn).get_table_names())
    assert {"instruments", "application_settings", "global_events", "alembic_version"} <= tables


def test_instrument_tables(data_root):
    expected = {
        "nodes", "node_lineage", "backtest_runs", "deeptest_scenarios", "deeptest_trades",
        "research_tasks", "research_findings", "research_sources",
        "live_sessions", "live_configs", "live_trades", "approvals", "activity_events",
        "alembic_version",
    }
    for sym in ("XAUUSD", "EURUSD", "BTCUSD"):
        eng = get_engine(instrument_url(sym, data_root))
        with eng.connect() as conn:
            tables = set(inspect(conn).get_table_names())
        assert expected <= tables, (sym, expected - tables)


def test_foreign_keys_enforced(data_root):
    eng = get_engine(instrument_url("XAUUSD", data_root))
    with eng.connect() as conn:
        mode = conn.execute(text("PRAGMA foreign_keys")).scalar()
    assert mode == 1
