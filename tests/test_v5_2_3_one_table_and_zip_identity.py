"""V5.2.3 §10/§22 — ONE scrollable table (no pages) and honest ZIP identity.

Two operator reports drove this file:

  1. "make sure there are no pages in LIVE TESTING or other tables — currently it
     shows 10 or 25 nodes and gives a next page; it should just list all of the
     nodes in one table and one scrollable page."

     So every *node* table (Live Testing, Deep Backtest, MT5 Backtest, Final
     Testing) and the deep-run trade list must fetch the WHOLE result set
     (``limit=0``) and render it in a single element that scrolls, with the header
     pinned — and the backend must be able to serve all rows while still telling
     the truth about a finite limit (``truncated``).

  2. The runtime-identity diagnostic reported ``GIT_IDENTITY_MATCH: FAIL`` with
     ``LOCAL GIT HEAD: None`` / ``fatal: not a git repository`` — because the
     dashboard was running from a GitHub **"Download ZIP"** export
     (``E:\\evolutionary-trading-lab-main``).  A checkout without ``.git`` must be
     *identified by fingerprint* (``BUILD_FINGERPRINTS.json``), not blamed, and
     the commit comparison must report ``N/A`` instead of a misleading PASS/FAIL.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
from app.db.database import Database
from app.research import populations as P

TOOLS = REPO / "backend" / "tools"
STYLES = REPO / "frontend" / "src" / "styles.css"
IDENTITY_FILE = REPO / "BUILD_FINGERPRINTS.json"
BAT = REPO / "CHECK_RUNNING_DASHBOARD.bat"

#: files that render node tables / trade tables and must NOT page any more
ONE_TABLE_FILES = {
    "Live Testing": REPO / "frontend" / "src" / "components" / "LiveNodeIndex.jsx",
    "Deep Backtest": REPO / "frontend" / "src" / "pages" / "DeepBacktest.jsx",
    "MT5 Backtest": REPO / "frontend" / "src" / "pages" / "Mt5Backtest.jsx",
    "Final Testing": REPO / "frontend" / "src" / "pages" / "FinalTesting.jsx",
    "Deep run trades": REPO / "frontend" / "src" / "components" / "HistoricalRunResults.jsx",
}

#: strings that only exist to page a table
PAGER_MARKERS = ("next »", "« prev", "Next →", "← Prev", "next ›", "‹ prev", "next ▶",
                 "◀ prev", "/ page", "rows per page", "PICKER_PAGE_SIZE",
                 "setPageOffset", "pageLimit")


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _genome(symbol="XAUUSD", timeframe="M15"):
    return {"symbol": symbol, "timeframe": timeframe, "direction": "long",
            "entry_long": {"op": "and", "clauses": [
                {"type": "compare", "left": "close", "cmp": ">", "right": "sma:50"}]},
            "entry_short": None,
            "exit": {"atr_spec": "atr:14", "sl_atr_mult": 1.5, "tp_atr_mult": 3.0}}


def _insert(db, node_id, *, status="VALID", data_source="USER_RESEARCH"):
    db.x("""INSERT OR REPLACE INTO strategies
            (id, hash, parent_id, generation, symbol, timeframe, direction, status,
             genome, complexity, fitness, created_at, updated_at, origin, run_id, data_source,
             research_node_num)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (node_id, f"h{node_id}", None, 39, "XAUUSD", "M15", "long", status,
          json.dumps(_genome()), 3, 1.0, 1.0, 1.0, "research", "RUN-20261007-000000",
          data_source, node_id))
    return node_id


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "v523_one_table.db"))
    d.x("DELETE FROM live_test_configs")
    # the research table's shortlist join (present in the shipped DATA schema)
    d.x("""CREATE TABLE IF NOT EXISTS research_shortlist (
               strategy_id INTEGER PRIMARY KEY, notes TEXT, created_at REAL NOT NULL)""")
    for i in range(7):
        _insert(d, 100 + i)
    _insert(d, 200, data_source="LEGACY_TEST")
    return d


# ===========================================================================
# A. the backend can hand over every row — and says when it did not
# ===========================================================================
def test_01_the_node_index_returns_every_row_when_limit_is_zero(db, monkeypatch):
    import app.api.routes as routes
    monkeypatch.setattr(routes, "get_db", lambda: db, raising=False)
    out = routes.nodes_index(filter="qualified", limit=0)
    assert out["all_rows"] is True
    assert out["truncated"] is False
    assert out["limit"] == 0
    assert out["returned"] == out["total"] == 7
    assert len(out["nodes"]) == 7, "every matching node is in the payload"

    paged = routes.nodes_index(filter="qualified", limit=5)
    assert paged["returned"] == 5 and paged["all_rows"] is False
    assert paged["truncated"] is True, "a page must never look like the population"
    assert paged["total"] == 7


def test_02_the_strategy_list_returns_every_row_when_limit_is_zero(db, monkeypatch):
    from app.stats import strategy_lab
    out = strategy_lab.strategy_list(db, limit=0)
    assert out["all_rows"] is True and out["truncated"] is False
    assert out["returned"] == out["total"] == 7
    assert out["pages"] == 1, "one scrollable table = one page"
    capped = strategy_lab.strategy_list(db, limit=3)
    assert capped["returned"] == 3 and capped["truncated"] is True and capped["pages"] == 3


def test_03_the_run_trade_list_returns_every_trade_when_limit_is_zero(monkeypatch):
    from app.historical_backtest import runs as hb
    import pandas as pd
    frame = pd.DataFrame([{"entry_ts": 1700000000 + i, "side": "buy", "lots": 0.01,
                           "pnl": 1.0 * i} for i in range(40)])
    monkeypatch.setattr(hb, "_trades_frame", lambda rid: frame, raising=False)
    monkeypatch.setattr(hb, "_row", lambda d, rid: {"status": "COMPLETE", "error": None},
                        raising=False)
    allrows = hb.run_trades("RUN-X", db=object(), limit=0)
    assert allrows["all_rows"] is True and allrows["truncated"] is False
    assert allrows["total"] == 40 and allrows["count"] == 40
    assert len(allrows["trades"]) == 40
    page = hb.run_trades("RUN-X", db=object(), limit=25)
    assert page["count"] == 25 and page["truncated"] is True and page["all_rows"] is False


# ===========================================================================
# B. the UI has no pager and one scrollable table
# ===========================================================================
def test_04_no_node_table_pages_any_more():
    for label, path in ONE_TABLE_FILES.items():
        src = path.read_text(encoding="utf-8")
        for marker in PAGER_MARKERS:
            assert marker not in src, f"{label} ({path.name}) still pages: {marker!r}"


def test_05_every_node_table_uses_the_one_scroll_container():
    for label, path in ONE_TABLE_FILES.items():
        src = path.read_text(encoding="utf-8")
        assert "table-scroll" in src, f"{label} ({path.name}) is not in one scrollable table"


def test_06_the_scroll_container_is_themed_and_pins_the_header():
    css = STYLES.read_text(encoding="utf-8")
    assert ".table-scroll" in css
    block = css.split(".table-scroll {", 1)[1].split("}", 1)[0]
    assert "overflow" in block and "max-height" in block
    # the sticky header rule the container relies on must still be in the theme
    header = css.split("table.tbl thead th", 1)[1].split("}", 1)[0]
    assert "position: sticky" in header


def test_07_the_tables_ask_for_all_rows():
    live = (REPO / "frontend" / "src" / "components" / "LiveNodeIndex.jsx").read_text(encoding="utf-8")
    deep = (REPO / "frontend" / "src" / "pages" / "DeepBacktest.jsx").read_text(encoding="utf-8")
    pick = (REPO / "frontend" / "src" / "pages" / "Mt5Backtest.jsx").read_text(encoding="utf-8")
    final = (REPO / "frontend" / "src" / "pages" / "FinalTesting.jsx").read_text(encoding="utf-8")
    assert "limit: 0, offset: 0" in live
    assert "limit: 0," in deep and "offset: 0," in deep
    assert "limit: 0, offset: 0" in pick
    assert "limit: 0," in final
    trades = (REPO / "frontend" / "src" / "components" / "HistoricalRunResults.jsx").read_text(encoding="utf-8")
    assert "{ limit: 0, offset: 0 }" in trades


# ===========================================================================
# C. a checkout without .git is identified by fingerprint, not blamed
# ===========================================================================
def _load_tool(name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_08_a_zip_checkout_is_reported_as_a_source_export(tmp_path):
    diag = _load_tool("dashboard_diagnostic")
    info = diag.git_identity(tmp_path, remote=False)
    assert info["checkout_kind"] == "source-export"
    assert info["has_git"] is False
    assert info["local_head"] is None and info["origin_main"] is None
    assert info["git_identity_match"] is None, "there is nothing to compare — never PASS/FAIL"
    assert ".git" in info["note"] and "Download ZIP" in info["note"]


def test_09_a_none_verdict_reads_na_not_fail():
    diag = _load_tool("dashboard_diagnostic")
    assert diag.verdict_line("GIT_IDENTITY_MATCH", None, na_note="no .git") == \
        "GIT_IDENTITY_MATCH: N/A   (no .git)"
    assert diag.verdict_line("GIT_IDENTITY_MATCH", True) == "GIT_IDENTITY_MATCH: PASS"
    assert diag.verdict_line("GIT_IDENTITY_MATCH", False) == "GIT_IDENTITY_MATCH: FAIL"


def test_10_the_operators_windows_build_is_identified_from_its_fingerprints():
    """His report: src 72fa9506… (61 files) + backend code 6266586e…, no .git."""
    bf = _load_tool("build_fingerprints")
    out = bf.match(REPO, fp={
        "src_hash": "72fa9506a777fcb6a6fb3a2034382b854199661043ddd9e0fe5c8935f5232771",
        "code_hash": "6266586e3fc025a702ff271429cbefdfec2f901d9feef10003e59ab29e3d6e19"})
    assert out["found"] is True
    assert out["release"] == "V5.2.1"
    assert out["commit_short"] == "7d9cc7f"
    assert out["is_latest"] is False


def test_11_an_unknown_tree_is_reported_unknown_never_guessed():
    bf = _load_tool("build_fingerprints")
    out = bf.match(REPO, fp={"src_hash": "deadbeef" * 8, "code_hash": "cafebabe" * 8})
    assert out["found"] is False and out["release"] is None
    assert out["latest_release"], "the index still states which release is newest"


def test_12_the_published_index_describes_this_tree():
    """Self-consistency gate: the release you ship must be identifiable."""
    bf = _load_tool("build_fingerprints")
    data = json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))
    releases = data["releases"]
    assert releases, "BUILD_FINGERPRINTS.json must carry at least one release"
    for r in releases:
        for key in ("release", "src_hash", "index_sha256", "entry_assets", "code_hash",
                    "recorded_at"):
            assert r.get(key), f"{r.get('release')} is missing {key}"
    now = bf.tree_fingerprints(REPO)
    assert bf.match(REPO, fp=now)["found"] is True, \
        "the current tree matches no published release — run build_fingerprints.py --record"


def test_13_the_launcher_reports_a_zip_checkout_instead_of_git_errors():
    raw = BAT.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n"), "the launcher must stay CRLF-only"
    text = raw.decode("ascii")
    assert "not exist" in text and "\\%ROOT%\\.git" in text or "%ROOT%\\.git" in text
    assert "THIS IS NOT A GIT CHECKOUT" in text
    assert "git clone https://github.com/awais225/evolutionary-trading-lab.git" in text
    assert "by FINGERPRINT" in text


def test_14_a_checkout_without_the_index_is_unknown_not_a_release(tmp_path):
    """"matches no published release" and "no index here at all" are different
    facts; a folder that ships no BUILD_FINGERPRINTS.json must not be blamed for
    failing a comparison it cannot make."""
    bf = _load_tool("build_fingerprints")
    out = bf.match(tmp_path, fp={"src_hash": "a" * 64, "code_hash": "b" * 64})
    assert out["found"] is False
    assert out["index_present"] is False
    assert out["index_name"] == "BUILD_FINGERPRINTS.json"
    assert out["releases_published"] == 0

    out2 = bf.match(REPO, fp={"src_hash": "a" * 64, "code_hash": "b" * 64})
    assert out2["found"] is False
    assert out2["index_present"] is True, "this repository publishes an index"
    assert out2["releases_published"] >= 3


def test_15_an_older_published_release_is_flagged_as_behind_not_as_latest():
    """A V5.2.1 or V5.2.2 tree (the operator's machine is V5.2.1) must be told it
    is older than the newest published release, so "which build do I run" has an
    answer that also says whether an update is waiting."""
    bf = _load_tool("build_fingerprints")
    data = json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))
    newest = data["releases"][-1]
    for old in data["releases"][:-1]:
        out = bf.match(REPO, fp={"src_hash": old["src_hash"], "code_hash": old["code_hash"]})
        assert out["found"] is True and out["release"] == old["release"]
        assert out["is_latest"] is False
        assert out["latest_release"] == newest["release"]

    out_new = bf.match(REPO, fp={"src_hash": newest["src_hash"], "code_hash": newest["code_hash"]})
    assert out_new["is_latest"] is True
    assert out_new["latest_release"] == newest["release"]


def test_16_the_dashboard_names_the_release_the_fingerprint_index_publishes():
    """The Build chip must not name a different build than the identity index
    does: ``/system/build`` reports PRODUCT_RELEASE, so that label has to equal
    the newest release in BUILD_FINGERPRINTS.json. It must also stay OUT of
    versions.manifest(), which is digested into stored experiments — naming a
    release must never invalidate DATA."""
    import json as _json

    from app import runtime_identity as ri
    from app.versions import PRODUCT_RELEASE, manifest

    published = _json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))["releases"]
    newest = published[-1]["release"]
    assert PRODUCT_RELEASE == newest, \
        f"the dashboard labels the build {PRODUCT_RELEASE} while the index publishes {newest}"

    report = ri.identity_report("identity-probe", REPO)
    assert report["release"] == PRODUCT_RELEASE
    assert PRODUCT_RELEASE not in _json.dumps(manifest(), sort_keys=True), \
        "the release label must stay outside the engine-generation manifest"
