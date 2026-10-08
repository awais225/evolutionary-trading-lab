"""V5.3 §4 — "Delete all previous data" must really reset the STUDY identity.

The operator's report
---------------------
A study of ~10,000 nodes was deleted with "Delete all previous data" and the new
study continued the old numbering instead of starting at 1.

The real cause (measured on the authoritative database, 2026-10-08)
-------------------------------------------------------------------
    LEGACY_TEST   rows: 787  ids 1..791
    USER_RESEARCH rows: 10000 ids 815..10814
    sqlite_sequence['strategies'] = 10814      <-- the high-water mark

`DELETE FROM strategies WHERE NOT <legacy>` removes the 10,000 USER rows and
leaves the LEGACY rows alone — but SQLite does NOT lower an AUTOINCREMENT
sequence on delete, so the first node of the fresh study was allocated
**id 10815**: the id range of the deleted study continued even though the rows
were gone. Every surface that labels a row `Node_<id>` therefore displayed the
old numbering, and the study-local number was never explicitly reset either.

What is pinned here
-------------------
1. the allocator is reset to the highest SURVIVING id (not left at the old mark);
2. the study-local node number of a fresh study starts at 1;
3. the LEGACY_TEST population is untouched (DATA rules);
4. a restart does not resurrect the deleted nodes.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LIFECYCLE = REPO / "backend" / "app" / "research_run" / "lifecycle.py"


def _lifecycle():
    """Import the app module directly (no app package import side effects)."""
    from app.research_run import lifecycle
    return lifecycle


# ===========================================================================
# fixtures: a scratch database that looks like the real one
# ===========================================================================
# The production classifier treats every row with id <= 787 as LEGACY_TEST
# infrastructure (the historical import), so a scratch database must be shaped the
# same way or it would protect its own research rows. These numbers mirror the
# authoritative database: LEGACY ids 1..791, USER_RESEARCH above them.
PRODUCTION_LEGACY_ROWS = 791


def _mk_db(tmp_path, *, user_nodes: int, legacy_nodes: int = PRODUCTION_LEGACY_ROWS,
           run_id: str = "RUN-20261008-010101"):
    from app.db.database import Database
    db = Database(str(tmp_path / "reset_identity.db"))

    def add(i: int, source: str, run: str, num: int) -> None:
        db.insert_strategy({"hash": f"{source}-{run}-{i}", "symbol": "XAUUSD",
                            "timeframe": "M15", "genome": {"seed": i},
                            "data_source": source, "run_id": run,
                            "research_node_num": num, "generation": 0, "status": "BORN"})

    for i in range(legacy_nodes):
        add(i, "LEGACY_TEST", "RUN-HISTORICAL-PRESERVED", i + 1)
    for i in range(user_nodes):
        add(1000 + i, "USER_RESEARCH", run_id, i + 1)
    return db


@pytest.fixture()
def scratch(tmp_path, monkeypatch):
    def _make(user_nodes=150, legacy_nodes=PRODUCTION_LEGACY_ROWS):
        db = _mk_db(tmp_path, user_nodes=user_nodes, legacy_nodes=legacy_nodes)
        lc = _lifecycle()
        monkeypatch.setattr(lc, "get_db", lambda: db)
        return db, lc, legacy_nodes
    return _make


def _seq(db, table="strategies"):
    row = db.one("SELECT seq FROM sqlite_sequence WHERE name=?", (table,))
    return int(row["seq"]) if row else 0


def _count(db, source):
    return int(db.one("SELECT COUNT(*) c FROM strategies WHERE data_source=?", (source,))["c"])


# ===========================================================================
# 1. the defect itself
# ===========================================================================
def test_01_the_allocator_used_to_continue_the_deleted_study(scratch):
    """Without the fix the next node continues the deleted study's numbering."""
    db, lc, legacy = scratch(user_nodes=150)
    before_next = db.one("SELECT COALESCE(MAX(id),0) m FROM strategies")["m"] + 1
    deleted = db.transaction([("DELETE FROM strategies WHERE " + lc.USER_ROWS, ())])
    surviving_max = db.one("SELECT COALESCE(MAX(id),0) m FROM strategies")["m"]
    assert deleted[0] == 150
    assert _count(db, "USER_RESEARCH") == 0 and _count(db, "LEGACY_TEST") == legacy
    # the rows are gone: an allocator that is NOT reset would keep handing out ids
    # above the old mark, i.e. the deleted study's range continues.
    assert _seq(db, "strategies") == before_next - 1
    assert _seq(db, "strategies") > surviving_max


def test_02_the_reset_lowers_the_allocator_to_the_surviving_population(scratch):
    db, lc, legacy = scratch(user_nodes=150)
    old_seq = _seq(db, "strategies")
    res = lc.reset_user_research()
    alloc = res["identity_allocator"]
    surviving_max = db.one("SELECT COALESCE(MAX(id),0) m FROM strategies")["m"]

    assert res["deleted_strategies"] == 150
    assert res["user_research_after"] == 0 and res["legacy_test_after"] == legacy
    assert res["legacy_untouched"] is True
    assert alloc["sequence_before"] == old_seq
    assert alloc["sequence_after"] == surviving_max, \
        "the sequence must land on the highest SURVIVING id, not stay at the deleted mark"
    assert alloc["reset"] is True
    assert alloc["next_strategy_id_after_reset"] == surviving_max + 1
    assert alloc["next_strategy_id_after_reset"] < old_seq + 1, \
        "the fresh study must not continue the deleted study's row numbering"
    assert alloc["next_node_number"] == 1


def test_03_the_first_node_of_the_fresh_study_is_node_1(scratch):
    db, lc, legacy = scratch(user_nodes=150)
    lc.reset_user_research()
    first = db.insert_strategy({"hash": "fresh-1", "symbol": "XAUUSD", "timeframe": "M15",
                                "genome": {"seed": 1}, "data_source": "USER_RESEARCH",
                                "run_id": "RUN-20261008-020202", "generation": 0, "status": "BORN"})
    second = db.insert_strategy({"hash": "fresh-2", "symbol": "XAUUSD", "timeframe": "M15",
                                 "genome": {"seed": 2}, "data_source": "USER_RESEARCH",
                                 "run_id": "RUN-20261008-020202", "generation": 0, "status": "BORN"})
    row1 = db.one("SELECT id, research_node_num, run_id FROM strategies WHERE id=?", (first,))
    row2 = db.one("SELECT id, research_node_num FROM strategies WHERE id=?", (second,))
    assert row1["research_node_num"] == 1, "the study-local node number restarts at 1"
    assert row2["research_node_num"] == 2
    assert row1["run_id"] == "RUN-20261008-020202"
    # and the row id is allocated from the surviving population, not the old mark
    assert first == legacy + 1 and second == legacy + 2


def test_04_a_second_reset_is_idempotent(scratch):
    db, lc, legacy = scratch(user_nodes=40)
    first = lc.reset_user_research()
    second = lc.reset_user_research()
    assert first["deleted_strategies"] == 40
    assert second["deleted_strategies"] == 0
    assert second["identity_allocator"]["sequence_after"] == legacy
    assert second["identity_allocator"]["next_strategy_id_after_reset"] == legacy + 1
    assert second["legacy_untouched"] is True


def test_05_legacy_rows_and_their_ids_survive(scratch):
    db, lc, legacy = scratch(user_nodes=150)
    legacy_ids = [r["id"] for r in db.q("SELECT id FROM strategies WHERE data_source=? "
                                        "ORDER BY id", ("LEGACY_TEST",))]
    lc.reset_user_research()
    after = [r["id"] for r in db.q("SELECT id FROM strategies WHERE data_source=? ORDER BY id",
                                   ("LEGACY_TEST",))]
    assert legacy_ids == after, "LEGACY_TEST rows keep their ids - they are never renumbered"


def test_06_a_restart_does_not_resurrect_the_deleted_nodes(scratch, tmp_path):
    db, lc, legacy = scratch(user_nodes=150)
    lc.reset_user_research()
    path = db.one("PRAGMA database_list")["file"] if False else None      # keep it explicit
    db_path = str(tmp_path / "reset_identity.db")
    db.close() if hasattr(db, "close") else None
    # a "restart": open the same file with a fresh connection
    con = sqlite3.connect(db_path)
    try:
        user = con.execute("SELECT COUNT(*) FROM strategies WHERE data_source='USER_RESEARCH'"
                           ).fetchone()[0]
        legacy_count = con.execute("SELECT COUNT(*) FROM strategies WHERE "
                                   "data_source='LEGACY_TEST'").fetchone()[0]
        seq = con.execute("SELECT seq FROM sqlite_sequence WHERE name='strategies'").fetchone()[0]
    finally:
        con.close()
    assert user == 0, "no deleted node reappears after a restart"
    assert legacy_count == legacy
    assert seq == legacy
    assert path is None


# ===========================================================================
# 2. the promise the operator sees (preview -> result)
# ===========================================================================
def test_07_the_preview_names_the_allocator_rule(scratch, monkeypatch):
    db, lc, legacy = scratch(user_nodes=150)
    monkeypatch.setattr(lc, "counts", lambda: {"user_research_nodes": 150,
                                               "legacy_test_nodes": legacy,
                                               "total_strategies": 150 + legacy,
                                               "max_user_generation": 0,
                                               "max_strategy_id": 150 + legacy})
    monkeypatch.setattr(lc, "_run_context", lambda: {"run_id": "RUN-20261008-010101", "run_nodes": 150,
                                                     "target": 150, "remaining_in_run": 0,
                                                     "active_run_id": "RUN-20261008-010101",
                                                     "engine_active_run_id": "RUN-20261008-010101"})
    monkeypatch.setattr(lc, "configured_fresh_target", lambda: 150)
    monkeypatch.setattr(lc, "list_research_backups", lambda limit=5: [])
    monkeypatch.setattr(lc, "operation_status", lambda: {})
    preview = lc.fresh_preview(mode="reset_only", target=150)
    assert preview["after_reset"]["first_node_number"] == 1
    assert preview["after_reset"]["identity_allocator"]["sequence_now"] == _seq(db)
    assert "never continues the deleted study's row numbering" in preview["id_policy"]
    assert "read_only" in preview and preview["read_only"] is True


def test_08_the_reset_result_carries_the_same_facts(scratch):
    db, lc, legacy = scratch(user_nodes=25)
    res = lc.reset_user_research()
    for key in ("identity_allocator", "sequences_reset"):
        assert key in res
    alloc = res["identity_allocator"]
    for key in ("sequence_before", "sequence_after", "next_strategy_id_before_reset",
                "next_strategy_id_after_reset", "next_node_number", "rule"):
        assert key in alloc, f"{key} must be reported so the operator can verify the reset"
    assert alloc["next_strategy_id_after_reset"] == alloc["max_surviving_id"] + 1


# ===========================================================================
# 3. the code contract (the fix must not be bypassed later)
# ===========================================================================
def test_09_the_deletes_and_the_allocator_reset_are_one_transaction():
    src = LIFECYCLE.read_text(encoding="utf-8")
    assert "IDENTITY_SEQUENCE_TABLES" in src
    assert "UPDATE sqlite_sequence SET seq=MAX(COALESCE((SELECT MAX(rowid) FROM %s),0)" in src
    assert "LEGACY_ID_CEILING" in src, \
        "a fresh study must never be allocated an id the classifier calls LEGACY"
    block = src.split("def _reset_statements", 1)[1].split("def reset_user_research", 1)[0]
    assert "sqlite_sequence" in block, \
        "the allocator reset must live in the same statement list as the deletes (atomic)"
    assert "DELETE FROM strategies WHERE" in block


def test_10_the_node_number_is_allocated_per_run_not_globally():
    """The study-local identity is the run the node belongs to."""
    db_src = (REPO / "backend" / "app" / "db" / "database.py").read_text(encoding="utf-8")
    block = db_src.split("research_node_num = s.get(\"research_node_num\")", 1)[1]
    block = block.split("try:", 1)[0]
    assert "WHERE run_id=?" in block
    assert "data_source=?" in block, \
        "when a row carries no run id the number is drawn from the study population"
