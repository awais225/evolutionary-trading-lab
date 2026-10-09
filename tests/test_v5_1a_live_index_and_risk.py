"""V5.1a §3/§5/§6/§8/§9/§10 — the live node index and the broker-aware risk maths.

Two groups of tests, both read-only with respect to the authoritative DATA:

1. **The qualified-node index** (``GET /api/nodes``) and the live node table
   (``GET /api/live-testing/nodes-table``). What is verified is exactly what the
   Live Testing page relies on:

   * the default filter is *qualified* — a FAILED node is never in the default
     view, and the failed/excluded/blocked buckets are reachable explicitly;
   * ``sort_by`` on return / PF / drawdown / win rate / trades / robustness /
     generation resolves to the row's real field (the alias map is echoed back as
     ``sort_key``) and really orders the rows — not "everything is None";
   * the schedule block is *decoded* (days is a list, conditions a dict). The
     stored columns are JSON text; returning them verbatim made a configured
     schedule render as an empty group in the browser.

2. **The broker-aware sizing preview** (``POST /api/mt5-execution/preview``) with
   a full broker symbol specification:

   * an explicit entry price makes the answer deterministic, and the volume
     scales with the money at risk and inversely with the stop distance;
   * BUY sizes from the ask and SELL from the bid when no entry is supplied;
   * a preview that cannot be computed reports *why* — it never returns a volume
     of 0 and never silently substitutes a price of 0.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load(name: str):
    p = Path(__file__).resolve().parent / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_v48 = _load("test_v4_8_dashboard.py")
_Bridge = _v48._Bridge
_Spec = _v48._Spec
_Quote = _v48._Quote

SYMBOL = "XAUUSD"
# V6.4 — the digits rule (MT5_BRIDGE_DETAILS.txt): XAUUSD (2-digit feed) has
# pip_size 0.01. The V5-era 0.10 constant made gold stops ten times too far.
PIP = 0.01


@pytest.fixture()
def broker(monkeypatch):
    """A read-only bridge with the broker's full XAUUSD specification.

    volume_min 0.01 / step 0.01, tick size 0.01 and tick value 1.00 per 0.01
    movement — so 45.00 of price distance costs 4500 per lot, which makes every
    expected number below computable by hand.
    """
    from app import mt5
    bridge = _Bridge(spec=_Spec(), quote=_Quote(bid=2400.0, ask=2400.3))
    monkeypatch.setattr(mt5, "get_bridge", lambda: bridge)
    return bridge


@pytest.fixture()
def quoteless_broker(monkeypatch):
    """A bridge whose symbol is fully specified but which has no quote yet."""
    from app import mt5
    bridge = _Bridge(spec=_Spec())
    bridge._quote = None            # _Bridge substitutes a default quote otherwise
    monkeypatch.setattr(mt5, "get_bridge", lambda: bridge)
    return bridge


@pytest.fixture()
def blind_broker(monkeypatch):
    """A bridge whose symbol specification has no tick size / tick value.

    This is what a thin bridge (or a terminal that has not finished loading the
    symbol) looks like: the maths is impossible, and the honest answer is an
    explicit refusal — not a lot size of 0.
    """
    from app import mt5
    spec = _Spec()
    spec.trade_tick_size = None
    spec.trade_tick_value = None
    bridge = _Bridge(spec=spec, quote=_Quote())
    monkeypatch.setattr(mt5, "get_bridge", lambda: bridge)
    return bridge


# ===========================================================================
# 1. §8/§9/§10 — the qualified-node index
# ===========================================================================
def test_01_the_default_filter_is_qualified_and_never_the_failed_bulk(client):
    d = client.get("/api/nodes?limit=200").json()
    assert d["ok"] is True
    assert d["filter"] == "qualified", "the index default must be Qualified/Alive/Eligible"
    buckets = {r["bucket"] for r in d["nodes"]}
    assert buckets <= {"qualified"}, f"the default view leaked {buckets - {'qualified'}}"
    assert d["total"] == d["counts"].get("qualified", 0)

    failed = client.get("/api/nodes?filter=failed&limit=200").json()
    assert failed["filter"] == "failed"
    assert {r["bucket"] for r in failed["nodes"]} <= {"failed"}
    assert failed["total"] == failed["counts"].get("failed", 0)
    # the default NEVER shows the failed bulk whatever the mix: only qualified
    # rows are listed and the failed bucket is fully reported under its own
    # filter. (The failed:qualified PROPORTION is study mix, not contract — the
    # operator's pristine snapshot is failed-heavy (9,872 vs 83) while a
    # later-stage study — or a run of these very research-run tests, which
    # rewrite the shared population's statuses — can hold more qualified nodes
    # than failed ones.)
    assert all(r["bucket"] == "qualified" for r in d["nodes"])
    assert d["total"] == d["counts"].get("qualified", 0)
    assert failed["total"] == failed["counts"].get("failed", 0)


def test_02_every_filter_is_reachable_and_reports_its_own_count(client):
    totals = None
    for f in ("qualified", "alive", "eligible", "all", "failed", "excluded", "blocked", "unknown"):
        d = client.get(f"/api/nodes?filter={f}&limit=5").json()
        assert d["ok"] is True, (f, d.get("error"))
        assert d["filter"] == f
        assert isinstance(d["total"], int)
        assert d["total"] == d["filter_totals"][f], (f, d["total"], d["filter_totals"])
        totals = d["filter_totals"]
    # the composite filters are sums of real buckets, never invented numbers
    d = client.get("/api/nodes?filter=all&limit=1").json()
    buckets = d["counts"]
    assert totals["all"] == sum(buckets.values()), (totals["all"], buckets)
    assert totals["all"] > totals["qualified"] + totals["failed"]
    # "alive" is qualified + the still-alive bucket, and can never be smaller
    assert totals["alive"] == buckets.get("qualified", 0) + buckets.get("alive", 0)
    assert totals["alive"] >= totals["qualified"]
    # the legacy exclusion is never hidden AND never invented: the excluded
    # bucket is EXACTLY the study's LEGACY_TEST population (787 on the
    # operator's mixed database, 0 in a user-research-only snapshot)
    assert totals["excluded"] == buckets.get("excluded", 0), "the exclusion is not hidden"
    from app.db.database import get_db
    n_legacy = get_db().q(
        "SELECT COUNT(*) AS n FROM strategies WHERE data_source='LEGACY_TEST'")[0]["n"]
    assert totals["excluded"] == n_legacy, (totals["excluded"], n_legacy)
    # (the failed:qualified proportion is study mix, not contract — see test_01;
    #  what IS contract is that every filter reports its own honest count)
    assert totals["failed"] == buckets.get("failed", 0)


def test_03_sorting_by_a_metric_actually_sorts(client):
    """§10 — sorting by return must order by the node's return, not by nothing.

    The values live in ``metrics`` / ``robustness``; a naive ``row.get(sort_by)``
    finds ``None`` for every row and the table silently stays in id order.
    """
    d = client.get("/api/nodes?filter=all&sort_by=return&sort_desc=true&limit=50").json()
    assert d["sort_key"] == "return_pct", d.get("sort_key")
    vals = [r["metrics"]["return_pct"] for r in d["nodes"]]
    assert any(v is not None for v in vals), "no node reported a return — the index would sort nothing"
    # every value that exists comes before any row without one (worst case: all
    # 10k value-less nodes leading the table and the sort looking broken)
    first_missing = next((i for i, v in enumerate(vals) if v is None), len(vals))
    assert all(v is not None for v in vals[:first_missing]), vals[:8]
    present = [v for v in vals if v is not None]
    assert present == sorted(present, reverse=True), present[:8]
    # and the sort really selected the high-return nodes rather than id order
    assert d["nodes"][0]["metrics"]["return_pct"] >= present[0] - 1e-12


def test_04_every_sort_key_the_page_offers_resolves(client):
    """The sort dropdown's keys must map to real fields (§10)."""
    expected = {"return": "return_pct", "profit_factor": "profit_factor",
                "max_drawdown_pct": "max_drawdown_pct", "win_rate": "win_rate",
                "trades": "trades", "robustness": "score", "generation": "generation",
                "fitness": "fitness", "node_id": "node_id"}
    for ui_key, field in expected.items():
        d = client.get(f"/api/nodes?filter=all&sort_by={ui_key}&limit=5").json()
        assert d["sort_key"] == field, (ui_key, d.get("sort_key"))

    asc = client.get("/api/nodes?filter=all&sort_by=trades&sort_desc=false&limit=50").json()
    allvals = [r["metrics"]["trades"] for r in asc["nodes"]]
    vals = [v for v in allvals if v is not None]
    assert vals == sorted(vals), vals[:8]
    n_missing = allvals.count(None)
    assert allvals[:len(allvals) - n_missing] == vals, "ascending order must also end with the missing values"


def test_05_the_schedule_block_is_decoded_not_raw_json_text(client):
    """A stored schedule is SQLite JSON text; the API must decode it.

    Symptom this guards: the operator saved Mon/Wed + London and the dialog
    reopened with every group unchecked, because the browser received
    ``"days": "[0, 2]"`` — a string, not a two-element list.
    """
    seen_configured = 0
    for filt in ("qualified", "alive", "all"):
        d = client.get(f"/api/nodes?filter={filt}&limit=200").json()
        for row in d["nodes"]:
            s = row["schedule"]
            assert not isinstance(s["days"], str), ("days arrived as text", row["node_id"], s["days"])
            assert not isinstance(s["sessions"], str), ("sessions arrived as text", row["node_id"])
            assert not isinstance(s["conditions"], str), ("conditions arrived as text", row["node_id"])
            assert not isinstance(s["windows"], str), ("windows arrived as text", row["node_id"])
            assert isinstance(s["enabled_explicit"], bool)
            assert isinstance(s["configured"], bool)
            if s["configured"]:
                seen_configured += 1
                assert s["description"], "a configured schedule must describe itself"
                if s["days"]:
                    assert all(isinstance(x, int) for x in s["days"]), s["days"]
                if isinstance(s["conditions"], dict):
                    assert all(isinstance(v, bool) for v in s["conditions"].values())
    assert seen_configured > 0, "no node in the live experiment has a stored schedule to check"


def test_06_the_live_node_table_also_decodes_the_schedule(client):
    d = client.get("/api/live-testing/nodes-table?limit=200").json()
    assert d.get("ok") is not False, d.get("error")
    rows = d.get("nodes") or []
    assert rows, "the live node table listed no live-testable node"
    for row in rows:
        s = row.get("schedule") or {}
        for key in ("days", "sessions", "regimes", "timeframes", "conditions", "windows"):
            assert not isinstance(s.get(key), str), (row.get("node_id"), key, s.get(key))
        assert s.get("description"), (row.get("node_id"), "no schedule description")
        assert row.get("node_id") is not None


def test_07_a_node_row_carries_the_research_record_the_page_shows(client):
    """§9 — node/experiment identity, metrics, robustness, coverage, risk, live."""
    d = client.get("/api/nodes?filter=all&limit=5").json()
    for row in d["nodes"]:
        for key in ("node_id", "node_label", "research_node_num", "generation", "symbol",
                    "timeframe", "signal_logic", "fitness", "bucket", "qualification",
                    "survival_evidence", "metrics", "robustness", "backtest_coverage",
                    "risk", "schedule", "live", "position"):
            assert key in row, key
        assert row["node_label"].startswith("Node #")
        assert str(row["node_id"]) in row["node_label"]
        assert row["experiment"], "the index must say which experiment the numbers belong to"


# ===========================================================================
# 2. §3/§5/§6 — broker-aware risk/lot preview
# ===========================================================================
def _preview(client, **body):
    r = client.post("/api/mt5-execution/preview", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_08_an_explicit_entry_makes_the_sizing_deterministic(client, broker):
    """V6.4 — $1 at 300 pips on XAUUSD with tick value 1.00 / tick size 0.01.

    Under the corrected XAUUSD pip (0.01) a 300-pip stop is a $3.00 price
    distance = 300 ticks of 0.01 = 300 currency per lot. $1 / 300 = 0.0033
    lots → rounded DOWN to the 0.01 step is below the 0.01 minimum, so the
    honest answer is a refusal, not 0.01 lots.
    """
    d = _preview(client, symbol=SYMBOL, side="BUY", entry=2400.0, sl_pips=300, risk_amount=1.0)
    assert d["levels"]["entry"] == pytest.approx(2400.0)
    assert d["levels"]["sl"] == pytest.approx(2400.0 - 300 * PIP)   # 2397.0
    assert d["levels"]["sl_from_pips"] is True
    assert d["sizing"] is None or d["sizing"].get("volume") is None
    assert d["blocked"]["code"] in ("VOLUME_BELOW_MINIMUM", "INVALID_SYMBOL_DATA")
    assert d["ok"] is False


def test_09_the_volume_scales_with_the_risk_and_inversely_with_the_stop(client, broker):
    """§5 — changing any input recalculates; nothing is cached from a previous answer."""
    small = _preview(client, symbol=SYMBOL, side="BUY", entry=2400.0, sl_pips=100,
                     risk_amount=3000.0)
    assert small["ok"] is True, small.get("blocked")
    v1 = small["sizing"]["volume"]
    assert v1 > 0

    big = _preview(client, symbol=SYMBOL, side="BUY", entry=2400.0, sl_pips=100,
                   risk_amount=6000.0)
    assert big["sizing"]["volume"] == pytest.approx(2 * v1, rel=1e-9), (v1, big["sizing"]["volume"])

    nearer = _preview(client, symbol=SYMBOL, side="BUY", entry=2400.0, sl_pips=50,
                      risk_amount=3000.0)
    assert nearer["sizing"]["volume"] == pytest.approx(2 * v1, rel=1e-9)

    # and the money actually at risk with the rounded volume is reported back
    for d in (small, big, nearer):
        assert d["sizing"]["actual_risk"] is not None
        assert d["sizing"]["actual_risk"] <= d["sizing"]["risk_amount"] + 1e-9


def test_10_buy_uses_the_ask_and_sell_uses_the_bid(client, broker):
    """§4 — BUY fills at the ask, SELL at the bid, and the levels follow the side."""
    buy = _preview(client, symbol=SYMBOL, side="BUY", sl_pips=100, tp_pips=100, risk_amount=3000.0)
    assert buy["levels"]["entry"] == pytest.approx(2400.3)               # the ask
    assert buy["levels"]["sl"] < buy["levels"]["entry"]                  # stop below
    assert buy["levels"]["tp"] > buy["levels"]["entry"]                  # target above

    sell = _preview(client, symbol=SYMBOL, side="SELL", sl_pips=100, tp_pips=100, risk_amount=3000.0)
    assert sell["levels"]["entry"] == pytest.approx(2400.0)              # the bid
    assert sell["levels"]["sl"] > sell["levels"]["entry"]
    assert sell["levels"]["tp"] < sell["levels"]["entry"]


def test_11_money_at_risk_below_the_broker_minimum_is_refused_with_the_numbers(client, broker):
    """§5 — no silent zero: the refusal names the minimum and the risk it needs.

    V6.4: with the corrected XAUUSD pip (0.01) a 300-pip stop risks 300 per
    lot, so $1 (0.0033 lots) is below the 0.01 minimum and is refused.
    """
    d = _preview(client, symbol=SYMBOL, side="BUY", entry=2400.0, sl_pips=300, risk_amount=1.0)
    assert d["ok"] is False
    assert d["blocked"]["code"] == "VOLUME_BELOW_MINIMUM"
    det = d["blocked"]["detail"]
    assert det["volume_min"] == pytest.approx(0.01)
    assert det["raw_volume"] == pytest.approx(1.0 / 300.0, abs=1e-8)   # rounded to 8 dp by the API
    assert det["risk_per_lot"] == pytest.approx(300.0)
    msg = d["blocked"]["message"]
    assert "0.01" in msg and "XAUUSD" in msg, msg


def test_12_an_unusable_broker_specification_names_the_missing_field(client, blind_broker):
    d = _preview(client, symbol=SYMBOL, side="BUY", entry=2400.0, sl_pips=300, risk_amount=1000.0)
    assert d["ok"] is False
    assert d["sizing"] is None or d["sizing"].get("volume") is None, "a volume must never be invented"
    assert d["blocked"]["code"] == "INVALID_SYMBOL_DATA"
    assert "tick" in d["blocked"]["message"].lower()
    # the spec is echoed so the UI can show which field is missing
    assert d["symbol_info"]["tick_size"] is None
    assert d["symbol_info"]["tick_value"] is None


def test_13_no_quote_and_no_entry_is_reported_not_made_up(client, quoteless_broker):
    """§5 — with neither a quote nor a typed price the answer is the reason.

    This is the state the panel is in before the first tick arrives, and the state
    the old code turned into "entry 0.00 → volume 0" instead of an explanation.
    """
    d = _preview(client, symbol=SYMBOL, side="BUY", sl_pips=300, risk_amount=10.0)
    assert d["ok"] is False
    assert d["levels"]["entry"] is None, "no entry may be invented when there is no quote"
    assert d["blocked"]["code"] in ("INVALID_ENTRY_PRICE", "MARKET_UNAVAILABLE"), d["blocked"]
    assert d["sizing"] is None or d["sizing"].get("volume") is None
    assert d["blocked"]["message"], "a refusal must carry the reason"
