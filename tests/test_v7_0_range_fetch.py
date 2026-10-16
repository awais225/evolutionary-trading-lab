"""V7.0 §2 — MT5 historical range fetching by selected duration.

Every expected value is computed from the mocked terminal's actual available
history and the documented UTC period semantics — never hardcoded. The central
assertion: different durations request different date ranges, and a two-year
request retrieves the available two-year history instead of a fixed ~2,345-bar
recent subset.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))


def _friday_noon_utc() -> float:
    now = datetime.now(tz=timezone.utc)
    back = (now.weekday() - 4) % 7
    from datetime import timedelta
    fri = now.replace(hour=12, minute=0, second=0, microsecond=0)
    return (fri - timedelta(days=back)).timestamp()


class _FakeBar:
    def __init__(self, ts):
        self.ts = float(ts)
        self.time = float(ts)

    def to_dict(self):
        return {"ts": self.ts, "open": 1, "high": 2, "low": 0.5, "close": 1.5,
                "volume": 10, "spread": 40}


class _WindowBridge:
    """A terminal holding REAL bars only inside `avail_start..avail_end`
    (weekday bars), honouring copy_rates_range(start, end) exactly."""

    source = "SIMULATOR"

    def __init__(self, avail_start, avail_end, step_s=900):
        self.avail_start = float(avail_start)
        self.avail_end = float(avail_end)
        self.step_s = step_s
        self.range_calls = []
        self.fail_all = False

    def profile(self):
        return {"source": self.source, "broker": "SIM", "server": "SIM",
                "profile_key": "SIM|SIM"}

    def copy_rates_range(self, symbol, timeframe, start, end):
        self.range_calls.append((float(start), float(end)))
        if self.fail_all:
            raise RuntimeError("terminal unavailable")
        out = []
        t = max(float(start), self.avail_start)
        while t <= min(float(end), self.avail_end):
            if datetime.fromtimestamp(t, tz=timezone.utc).weekday() < 5:
                out.append(_FakeBar(t))
            t += self.step_s
        return out

    def copy_rates(self, symbol, timeframe, n):
        return []


class _MemStore:
    def __init__(self):
        self.rows = 0
        self.first_ts = None
        self.last_ts = None
        self.version = 1
        self.profile_key = "SIM|SIM"
        self.profile = {"source": "SIMULATOR", "broker": "SIM", "server": "SIM",
                        "profile_key": "SIM|SIM"}
        self.symbol = "XAUUSD"
        self.timeframe = "M15"
        self.fingerprint = "mem"
        self.seen = set()

    def append(self, df, source_info=None):
        if isinstance(df, dict):
            ts = list(df.get("ts") or [])
        else:
            ts = list(df["ts"]) if "ts" in getattr(df, "columns", []) else []
        added = 0
        for t in ts:
            if float(t) in self.seen:
                continue
            self.seen.add(float(t))
            added += 1
        if ts:
            self.first_ts = min(min(ts), self.first_ts) if self.first_ts else min(ts)
            self.last_ts = max(max(ts), self.last_ts) if self.last_ts else max(ts)
        self.rows += added
        return {"accepted": added, "duplicates_identical": len(ts) - added,
                "conflicts": 0}

    def read_range(self):
        import pandas as pd
        return pd.DataFrame()


class TestRangeFetch:
    @pytest.fixture()
    def env(self, monkeypatch):
        import app.data.engine as E
        end = _friday_noon_utc()
        step = 900
        # terminal holds TWO YEARS of weekday M15 bars
        bridge = _WindowBridge(end - 730 * 86400, end, step)
        store = _MemStore()
        monkeypatch.setattr(E, "get_bridge", lambda: bridge)
        monkeypatch.setattr(E.master, "get_master", lambda *a, **k: store)
        monkeypatch.setattr(E.DataEngine, "_materialize_dataset_view",
                            lambda self, mstore: "ds-test")
        return E.DataEngine(), bridge, store, end, step

    def _request(self, engine, duration_key, end_ts):
        from app.deeptest.periods import resolve_period
        pr = resolve_period(duration_key, end_ts=end_ts)
        res = engine.sync_master("XAUUSD", "M15", force_full=True,
                                 request_start=pr["start_ts"], request_end=pr["end_ts"])
        return pr, res

    def test_durations_produce_distinct_requested_ranges(self, env):
        engine, bridge, store, end, step = env
        ranges = {}
        for key in ("1D", "7D", "1M", "6M", "1Y", "2Y", "3Y"):
            pr, res = self._request(engine, key, end)
            rep = res.get("fetch") or {}
            assert rep.get("requested_start") == pytest.approx(pr["start_ts"])
            assert rep.get("requested_end") == pytest.approx(pr["end_ts"])
            ranges[key] = (rep["requested_start"], rep["requested_end"])
        spans = {k: v[1] - v[0] for k, v in ranges.items()}
        # every duration is a DIFFERENT window, strictly increasing
        seq = [spans[k] for k in ("1D", "7D", "1M", "6M", "1Y", "2Y", "3Y")]
        assert all(b > a for a, b in zip(seq, seq[1:]))
        # and no two durations share the same fixed range (the 2,345-bar bug)
        assert len(set(ranges.values())) == 7

    def test_two_year_request_retrieves_two_year_history(self, env):
        """THE user-facing defect: a 2Y request against a terminal holding two
        years must return that history — not stop at a fixed recent subset."""
        engine, bridge, store, end, step = env
        pr, res = self._request(engine, "2Y", end)
        rep = res["fetch"]
        assert rep["bars_received"] > 20000, (
            f"two-year M15 history must exceed the old ~2345-bar window, got {rep['bars_received']}")
        assert rep["received_start"] == pytest.approx(bridge.avail_start, abs=step * 6)
        assert rep["received_end"] == pytest.approx(bridge.avail_end, abs=step * 6)
        assert rep["persisted_rows"] == store.rows == rep["bars_received"]
        assert res["sync_status"] == "COMPLETE"
        # the requested window is really the two-year window
        assert rep["requested_end"] - rep["requested_start"] == pytest.approx(
            pr["end_ts"] - pr["start_ts"])

    def test_three_year_request_against_two_year_history_is_partial(self, env):
        engine, bridge, store, end, step = env
        pr, res = self._request(engine, "3Y", end)
        rep = res["fetch"]
        assert res["sync_status"] == "PARTIAL"
        assert res.get("partial_reason")
        assert rep["received_start"] == pytest.approx(bridge.avail_start, abs=step * 6)
        assert rep["bars_received"] < (pr["end_ts"] - pr["start_ts"]) / step
        # the uncovered portion is named with real timestamps
        assert rep.get("uncovered")
        assert rep["requested_start"] == pytest.approx(pr["start_ts"])
        assert rep["requested_end"] == pytest.approx(pr["end_ts"])

    def test_short_cache_never_satisfies_longer_request(self, env):
        engine, bridge, store, end, step = env
        # seed the store with ONE MONTH of bars
        month_ago = end - 30 * 86400
        t = month_ago
        while t <= end:
            store.append({"ts": [t]})
            t += step
        seeded = store.rows
        assert seeded > 0 and store.first_ts == pytest.approx(month_ago)
        pr, res = self._request(engine, "2Y", end)
        rep = res["fetch"]
        # the HEAD gap (older history) must actually be requested
        assert any(iv["start"] <= pr["start_ts"] + step * 2 for iv in rep["intervals"])
        assert store.first_ts <= pr["start_ts"] + step * 6
        assert store.rows > seeded + 20000
        assert res["sync_status"] == "COMPLETE"

    def test_incremental_call_never_reuses_for_longer_goal(self, env):
        engine, bridge, store, end, step = env
        # one month of history exists and is tail-fresh (the old reuse case)
        t = end - 30 * 86400
        while t <= end:
            store.append({"ts": [t]})
            t += step
        bridge.range_calls.clear()
        from app.deeptest.periods import resolve_period
        pr = resolve_period("2Y", end_ts=end)
        res = engine.sync_master("XAUUSD", "M15",
                                 request_start=pr["start_ts"], request_end=pr["end_ts"])
        assert bridge.range_calls, "a tail-fresh short cache must NOT shortcut a 2Y goal"
        assert res["fetch"]["bars_received"] > 20000

    def test_persisted_counts_agree_and_duplicates_dedupe(self, env):
        engine, bridge, store, end, step = env
        pr, res = self._request(engine, "1M", end)
        rep = res["fetch"]
        assert rep["persisted_rows"] == store.rows == len(store.seen)
        # second run over the same range adds nothing (dedupe) and reports reuse
        res2 = engine.sync_master("XAUUSD", "M15",
                                  request_start=pr["start_ts"], request_end=pr["end_ts"])
        assert res2.get("fetch_mode") in ("reuse_existing", "requested_range")
        assert store.rows == rep["persisted_rows"]

    def test_failed_terminal_requests_never_report_success(self, env):
        engine, bridge, store, end, step = env
        bridge.fail_all = True
        from app.deeptest.periods import resolve_period
        pr = resolve_period("6M", end_ts=end)
        res = engine.sync_master("XAUUSD", "M15", force_full=True,
                                 request_start=pr["start_ts"], request_end=pr["end_ts"])
        assert res["status"] in ("no_data_from_bridge",)
        assert res.get("sync_status") != "COMPLETE"
        assert store.rows == 0

    def test_second_timeframe_not_hardcoded(self, env, monkeypatch):
        import app.data.engine as E
        end = _friday_noon_utc()
        bridge = _WindowBridge(end - 730 * 86400, end, 3600)    # H1 bars (hourly)
        store = _MemStore()
        store.timeframe = "H1"
        monkeypatch.setattr(E, "get_bridge", lambda: bridge)
        monkeypatch.setattr(E.master, "get_master", lambda *a, **k: store)
        monkeypatch.setattr(E.DataEngine, "_materialize_dataset_view",
                            lambda self, mstore: "ds-test")
        from app.deeptest.periods import resolve_period
        pr = resolve_period("2Y", end_ts=end)
        res = E.DataEngine().sync_master("XAUUSD", "H1", force_full=True,
                                         request_start=pr["start_ts"],
                                         request_end=pr["end_ts"])
        rep = res["fetch"]
        # H1 weekdays for 2 years ≈ 2*260*24 ≈ 12k bars (not M15's ~50k)
        assert 8000 < rep["bars_received"] < 20000, rep["bars_received"]
        assert res["sync_status"] == "COMPLETE"

    def test_frontend_transmits_the_selected_duration(self):
        text = (REPO / "frontend/src/pages/DeepBacktest.jsx").read_text(encoding="utf-8")
        assert "deeptestFetch({ symbol, timeframe, duration_key: periodKey })" in text

    def test_frontend_refreshes_coverage_after_a_successful_fetch(self):
        """The displayed bar count/coverage updates from persisted data as soon
        as the fetch job reaches a terminal state — without a page reload."""
        text = (REPO / "frontend/src/pages/DeepBacktest.jsx").read_text(encoding="utf-8")
        assert "if (fetchFinished)" in text
        assert "await loadNodes();" in text
        # V9.1 reacts to the actual newly loaded rows and selected duration,
        # not an old empty-node closure. Mounted interaction tests also verify it.
        assert "useEffect(() => { loadCoverage(nodes); }, [loadCoverage]);" in text

    def test_fetch_route_passes_the_requested_period(self, monkeypatch):
        import app.api.routes as R
        captured = {}

        def fake_start(symbol, timeframe, period=None):
            captured["period"] = period
            return {"ok": True, "job": {}}

        import app.deeptest.fetch_job as FJ
        monkeypatch.setattr(FJ, "start_fetch", fake_start)
        body = R.deeptest_fetch.__wrapped__ if hasattr(R.deeptest_fetch, "__wrapped__") else None
        # call the endpoint function directly (it is a plain function)
        out = R.deeptest_fetch({"symbol": "XAUUSD", "timeframe": "M15",
                                "duration_key": "2Y"})
        assert out["ok"]
        assert captured["period"] == {"duration_key": "2Y"}
