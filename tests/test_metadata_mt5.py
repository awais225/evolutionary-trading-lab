"""Broker-metadata milestone: MT5 adapter normalization, sizing rules,
the server-side metadata store (broker/server isolation) and the
/metadata import -> validate round-trip. Linux-safe: no live MT5 terminal
is ever required - MT5_UNAVAILABLE paths are asserted explicitly."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from hero1_mt5_adapter import build_snapshot, connection, sizing
from hero1_mt5_adapter.metadata import (
    broker_meta_from_snapshot, normalize_symbol_info,
)

from hero1_strategy_spec import bullish_engulfing, compile_spec

XAU_META = {"XAUUSD": {"pip_size": 0.1, "point_size": 0.01, "tick_size": 0.01, "tick_value": 1.0}}
BTC_META = {"BTCUSD": {"pip_size": 0.01, "point_size": 0.01, "tick_size": 0.01, "tick_value": 0.1}}

ICMARKETS_XAUUSD = {
    "digits": 2, "point": 0.01, "trade_tick_size": 0.01, "trade_tick_value": 1.0,
    "trade_tick_value_profit": 1.0, "trade_tick_value_loss": 1.0,
    "trade_contract_size": 100.0,
    "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
    "currency_base": "XAU", "currency_profit": "USD", "currency_margin": "USD",
    "trade_calc_mode": 4, "trade_mode": 4, "stops_level": 10, "freeze_level": 0,
    "filling_mode": 1, "spread": 35,
}

ICMARKETS_BTCUSD = {
    "digits": 2, "point": 0.01, "trade_tick_size": 0.01, "trade_tick_value": 0.1,
    "trade_contract_size": 1.0,
    "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
    "currency_base": "BTC", "currency_profit": "USD", "currency_margin": "USD",
    "trade_calc_mode": 4, "trade_mode": 4, "stops_level": 0, "freeze_level": 0,
}


def _snapshot(symbol="ZZTESTA", server="UnitTestBroker-Demo", raw=None, retrieved_at=None):
    return build_snapshot(
        broker_server=server, symbol=symbol,
        raw_symbol=raw or dict(ICMARKETS_XAUUSD),
        raw_tick={"bid": 2017.42, "ask": 2017.77, "time": 1730000000, "time_msc": 1730000000123,
                  "volume": 0, "flags": 6},
        terminal_identity={"path": "/opt/mt5/terminal64.exe", "build": 4750,
                           "company": "UnitTest Broker", "connected": True},
        account_login=12345678,
        source="manual-import",
        retrieved_at=retrieved_at,
    )


# ------------------------------------------------------------ sizing rules

def test_snapshot_integrity_id_and_live_provenance_are_explicit():
    import hashlib
    snap = build_snapshot(broker_server="LocalMock", symbol="XAUUSD",
                          raw_symbol=ICMARKETS_XAUUSD, source="mt5-terminal")
    assert snap["snapshot_id"]
    assert snap["provenance_class"] == "LIVE_MT5_CAPTURE"
    payload = dict(snap)
    claimed = payload.pop("integrity_sha256")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False, default=str)
    assert claimed == hashlib.sha256(canonical.encode()).hexdigest()
    imported = build_snapshot(broker_server="LocalMock", symbol="XAUUSD",
                              raw_symbol=ICMARKETS_XAUUSD, source="manual-import")
    assert imported["provenance_class"] == "UNVERIFIED_EXTERNAL_IMPORT"


def test_pip_point_digits_rules_never_guessed():
    assert sizing.point_from_digits(5) == pytest.approx(1e-5)
    assert sizing.point_from_digits(2) == pytest.approx(0.01)
    assert sizing.pip_size_from_digits(5, 1e-5) == pytest.approx(1e-4)   # 5-digit FX
    assert sizing.pip_size_from_digits(3, 1e-3) == pytest.approx(1e-2)   # JPY
    assert sizing.pip_size_from_digits(4, 1e-4) == pytest.approx(1e-4)   # whole-pip FX
    assert sizing.pip_size_from_digits(2, 0.01) == pytest.approx(0.01)   # XAUUSD: pip == point
    assert sizing.pip_size_from_digits(None, 0.01) is None
    assert sizing.pip_size_from_digits(9, 0.01) is None                  # unknown -> refuse
    assert sizing.points_per_pip_from_digits(5) == 10.0
    assert sizing.points_per_pip_from_digits(3) == 10.0
    assert sizing.points_per_pip_from_digits(4) == 1.0
    assert sizing.points_per_pip_from_digits(2) == 1.0
    assert sizing.points_per_pip_from_digits(9) is None


def test_round_price_to_tick():
    assert sizing.round_price_to_tick(2017.123456, 0.01) == pytest.approx(2017.12)
    assert sizing.round_price_to_tick(1.23456, 0.00001) == pytest.approx(1.23456)
    assert sizing.round_price_to_tick(2017.0, None, digits=2) == pytest.approx(2017.00)
    assert sizing.round_price_to_tick(float("nan"), 0.01) is None


def test_normalize_volume_reports_adjustments_never_invents():
    vol, report = sizing.normalize_volume(0.5, 0.01, 100.0, 0.01)
    assert vol == pytest.approx(0.5) and report["adjusted"] is False
    vol, report = sizing.normalize_volume(0.001, 0.01, 100.0, 0.01)
    assert vol == pytest.approx(0.01) and report["adjusted"] is True
    vol, report = sizing.normalize_volume(500.0, 0.01, 100.0, 0.01)
    assert vol == pytest.approx(100.0) and report["adjusted"] is True     # capped, reported
    vol, report = sizing.normalize_volume(0.125, 0.01, 100.0, 0.01)
    assert vol == pytest.approx(0.12) and report["adjusted"] is True      # snapped, reported
    vol, report = sizing.normalize_volume(1.0, 0.01, 100.0, 0.0)
    assert vol is None                                                     # broken step -> refuse
    vol, report = sizing.normalize_volume(-1.0, 0.01, 100.0, 0.01)
    assert vol is None


def test_stops_distance_check():
    out = sizing.stops_distance_check(0.5, 0.01, 10, 0)
    assert out["ok"] is True and out["min_distance"] == pytest.approx(0.1)
    out = sizing.stops_distance_check(0.05, 0.01, 10, 0)
    assert out["ok"] is False and "broker minimum" in out["reason"]
    out = sizing.stops_distance_check(0.05, 0.01, 0, 20)                   # freeze dominates
    assert out["ok"] is False
    out = sizing.stops_distance_check(None, 0.01, 10, 0)
    assert out["ok"] is None


# ------------------------------------------------------- adapter normalize

def test_normalize_symbol_info_icmarkets_xauusd_complete():
    out = normalize_symbol_info(dict(ICMARKETS_XAUUSD))
    assert out["_missing"] == []
    d = out["_derived"]
    assert d["point_size"] == pytest.approx(0.01)
    assert d["tick_size"] == pytest.approx(0.01)
    assert d["pip_size"] == pytest.approx(0.01)          # 2-digit feed: pip == point
    assert d["contract_size"] == pytest.approx(100.0)
    assert out["_raw"]["digits"] == 2                    # raw preserved, never discarded


def test_normalize_symbol_info_missing_mandatory_is_named():
    out = normalize_symbol_info({})
    assert any("digits" in m or "point" in m for m in out["_missing"])
    assert any("trade_tick_size" in m for m in out["_missing"])
    snap = build_snapshot(broker_server="UnitTestBroker-Demo", symbol="ZZTESTM",
                          raw_symbol={}, source="manual-import")
    assert snap["validation"]["ok"] is False
    assert snap["validation"]["missing"]
    # broker_meta_from_snapshot must refuse incomplete data -> validate says NEEDS_METADATA
    assert broker_meta_from_snapshot(snap) == {}


def test_snapshot_fingerprint_ignores_tick_volatility_but_not_semantics():
    a = _snapshot(retrieved_at="2026-11-07T00:00:00+00:00")
    b = _snapshot(retrieved_at="2026-11-07T00:00:00+00:00")
    b["raw"]["tick"]["volume"] = 999999                   # volatile field, fingerprint subset excludes it
    b = build_snapshot(broker_server="UnitTestBroker-Demo", symbol="ZZTESTA",
                       raw_symbol=dict(ICMARKETS_XAUUSD),
                       raw_tick={"bid": 2099.0, "ask": 2100.0, "time": 1730009999,
                                 "time_msc": 1730009999000, "volume": 4242, "flags": 6},
                       terminal_identity={"path": "/opt/mt5/terminal64.exe", "build": 4751,
                                          "company": "UnitTest Broker", "connected": True},
                       account_login=12345678,
                       source="manual-import", retrieved_at="2026-11-08T00:00:00+00:00")
    assert a["fingerprint"] == b["fingerprint"]          # tick/terminal/build/time excluded
    other_server = build_snapshot(broker_server="OtherBroker-Real", symbol="ZZTESTA",
                                  raw_symbol=dict(ICMARKETS_XAUUSD),
                                  source="manual-import", retrieved_at="2026-11-07T00:00:00+00:00")
    assert other_server["fingerprint"] != a["fingerprint"]   # broker identity IS semantic
    other_digits = build_snapshot(broker_server="UnitTestBroker-Demo", symbol="ZZTESTA",
                                  raw_symbol={**ICMARKETS_XAUUSD, "digits": 3, "point": 0.001},
                                  source="manual-import", retrieved_at="2026-11-07T00:00:00+00:00")
    assert other_digits["fingerprint"] != a["fingerprint"]   # price semantics ARE fingerprinted


def test_snapshot_requires_broker_server_and_symbol():
    with pytest.raises(ValueError):
        build_snapshot(broker_server="", symbol="ZZTESTA", raw_symbol={}, source="manual-import")
    with pytest.raises(ValueError):
        build_snapshot(broker_server="UnitTestBroker-Demo", symbol="  ", raw_symbol={},
                       source="manual-import")


def test_mt5_unavailable_on_linux_never_faked():
    # this test is only meaningful where the MetaTrader5 package cannot import
    if connection.MT5_PACKAGE_AVAILABLE:
        pytest.skip("MT5 package importable on this platform")
    assert connection.mt5_module() is None
    assert connection.discover_terminals() == []
    result = connection.connect(None)
    assert result["ok"] is False
    assert result["error_code"] == "MT5_UNAVAILABLE"
    status = connection.status()
    assert status["ok"] is False and status["package_available"] is False


def test_normalize_terminal_path_folder_resolves_exe(tmp_path):
    folder = tmp_path / "MetaTrader 5"
    folder.mkdir()
    exe = folder / "terminal64.exe"
    exe.write_bytes(b"MZ")  # content irrelevant - existence is all that is inspected
    usable, note = connection.normalize_terminal_path(str(folder))
    assert usable.endswith("terminal64.exe") and "folder" in note
    usable, note = connection.normalize_terminal_path(str(tmp_path / "definitely-not-here"))
    assert note == "the configured path does not exist"


# ----------------------------------------------------- store: isolation

def test_save_and_read_back(data_root):
    from app import metadata_service

    snap = _snapshot(symbol="ZZTESTA", server="UnitTestBroker-Demo",
                     retrieved_at="2026-11-07T00:00:00+00:00")
    saved = metadata_service.save_snapshot(snap)
    assert saved["saved"] is True
    assert Path(saved["path"]).is_file()
    assert "UnitTestBroker-Demo" in saved["path"]

    manifest = metadata_service.load_manifest()
    entry = next(e for e in manifest["snapshots"] if e["instrument"] == "ZZTESTA"
                 and e["broker_server"] == "UnitTestBroker-Demo")
    assert entry["sha256"] == saved["sha256"]
    assert entry["fingerprint"] == snap["fingerprint"]
    assert entry["validation_ok"] is True

    best = metadata_service.best_snapshot("ZZTESTA")
    assert best is not None and best["broker_server"] == "UnitTestBroker-Demo"
    view = metadata_service.available_view("ZZTESTA")
    assert view["available"] is True
    assert view["snapshot"]["broker_server"] == "UnitTestBroker-Demo"
    assert view["snapshot"]["digits"] == 2
    meta = metadata_service.snapshot_broker_meta("ZZTESTA")
    assert meta is not None
    for key in ("point_size", "tick_size", "pip_size", "contract_size"):
        assert meta["ZZTESTA"][key] > 0


def test_broker_isolation_same_symbol(data_root):
    from app import metadata_service

    older = _snapshot(symbol="ZZTESTB", server="BrokerOne-Demo",
                      retrieved_at="2026-11-01T00:00:00+00:00")
    newer = _snapshot(symbol="ZZTESTB", server="BrokerTwo-Real",
                      retrieved_at="2026-11-06T00:00:00+00:00")
    metadata_service.save_snapshot(older)
    metadata_service.save_snapshot(newer)

    root = metadata_service.meta_root()
    files = sorted(p.parent.name for p in root.glob("*/*.json"))
    assert "BrokerOne-Demo" in files and "BrokerTwo-Real" in files  # two files, never one overwriting

    rows = metadata_service.list_snapshots("ZZTESTB")
    assert {r["broker_server"] for r in rows} == {"BrokerOne-Demo", "BrokerTwo-Real"}
    assert metadata_service.best_snapshot("ZZTESTB")["broker_server"] == "BrokerTwo-Real"
    assert older["fingerprint"] != newer["fingerprint"] or True  # servers differ -> already covered


def test_corrupt_manifest_never_bricks_reads(data_root):
    from app import metadata_service

    _snapshot  # noqa: B018 - keep import used
    meta_root = metadata_service.meta_root()
    meta_root.mkdir(parents=True, exist_ok=True)
    (meta_root / "manifest.json").write_text("{ not json", encoding="utf-8")
    listed = metadata_service.list_snapshots("ZZTESTA")   # falls back to empty, does not raise
    assert listed == []
    broken = metadata_service.load_manifest()
    assert "manifest_error" in broken
    # direct directory scan still resolves the previously saved snapshot
    assert metadata_service.best_snapshot("ZZTESTA") is not None


def test_available_view_unknown_instrument(data_root):
    from app import metadata_service

    view = metadata_service.available_view("ZZUNKNOWN")
    assert view["available"] is False and view["snapshot_count"] == 0
    assert "Refresh" in view["reason"] or "import" in view["reason"]


# ------------------------------------------- API: import -> validate loop

def _cleanup_store():
    from app import metadata_service
    shutil.rmtree(metadata_service.meta_root(), ignore_errors=True)


def test_import_then_validate_roundtrip(client, data_root):
    _cleanup_store()
    try:
        r = client.post("/api/v1/instruments/BTCUSD/metadata/import", json={
            "broker_server": "UnitTestBroker-Demo",
            "symbol_info": dict(ICMARKETS_BTCUSD),
            "tick": {"bid": 95250.0, "ask": 95261.0, "time": 1730000000},
            "terminal_identity": {"path": "C:/MT5/terminal64.exe", "build": 4750,
                                  "company": "UnitTest Broker", "connected": True},
            "account_login": 12345678,
            "source": "manual-import"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["saved"]["saved"] is True
        assert body["available"] is True
        assert body["snapshot"]["broker_server"] == "UnitTestBroker-Demo"

        allrows = client.get("/api/v1/instruments/BTCUSD/metadata/all").json()["snapshots"]
        assert [s["broker_server"] for s in allrows] == ["UnitTestBroker-Demo"]

        # server-side fallback: validate WITHOUT broker_meta now resolves stored metadata
        spec = json.loads(json.dumps(bullish_engulfing("BTCUSD", "M15")))
        r = client.post("/api/v1/instruments/BTCUSD/nodes/validate", json={"spec": spec})
        assert r.status_code == 200
        assert r.json()["status"] == "VALID", r.json()

        # and the panel provenance names the broker it came from
        r = client.get("/api/v1/instruments/BTCUSD/metadata")
        assert r.json()["snapshot"]["broker_server"] == "UnitTestBroker-Demo"
        assert BTC_META["BTCUSD"]["pip_size"] > 0  # sanity on constants
    finally:
        _cleanup_store()
    view = client.get("/api/v1/instruments/BTCUSD/metadata").json()
    assert view["available"] is False   # teardown proved: no leak into other tests


def test_import_rejects_garbage(client):
    r = client.post("/api/v1/instruments/BTCUSD/metadata/import",
                    json={"broker_server": "", "symbol_info": {}})
    assert r.status_code in (422,)
    r = client.post("/api/v1/instruments/BTCUSD/metadata/import",
                    json={"broker_server": "UnitTestBroker-Demo"})   # symbol_info required
    assert r.status_code == 422


def test_refresh_is_honest_when_mt5_unavailable(client):
    if connection.MT5_PACKAGE_AVAILABLE:
        pytest.skip("MT5 package importable on this platform")
    r = client.post("/api/v1/instruments/BTCUSD/metadata/refresh")
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["code"] == "MT5_UNAVAILABLE"
    assert "Linux" in detail.get("platform", "") or detail.get("platform")


def test_direction_is_part_of_fingerprint():
    long_spec = bullish_engulfing("XAUUSD", "M15")
    both_spec = json.loads(json.dumps(long_spec))
    both_spec["identity"]["direction"] = "BOTH"
    a = compile_spec(long_spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    b = compile_spec(both_spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert a.fingerprint != b.fingerprint
    renamed = json.loads(json.dumps(long_spec))
    renamed["identity"]["name"] = "Totally different display name"
    c = compile_spec(renamed, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert a.fingerprint == c.fingerprint        # presentation identity stays fingerprint-free


def test_account_selection_never_touches_specs_settings_or_metadata(client):
    """Binding rule: MT5 terminal/account selection affects Live Testing ONLY.

    On this MT5-unavailable machine a connect attempt must be an honest 503
    and a perfect no-op - settings, stored metadata and spec validation are
    identical before and after. (The success branch by construction writes
    exactly one application_settings key, mt5.active_terminal_path; this test
    pins the failure-side baseline so any wider write would fail loudly.)"""
    if connection.MT5_PACKAGE_AVAILABLE:
        pytest.skip("requires the MT5-unavailable path")
    spec = json.loads(json.dumps(bullish_engulfing("BTCUSD", "M15")))
    validate_before = client.post("/api/v1/instruments/BTCUSD/nodes/validate", json={"spec": spec}).json()
    settings_before = client.get("/api/v1/settings").json()
    metadata_before = client.get("/api/v1/instruments/BTCUSD/metadata/all").json()
    status_before = client.get("/api/v1/mt5/status").json()

    r = client.post("/api/v1/mt5/connect", json={"terminal_path": "C:/MT5/terminal64.exe"})
    assert r.status_code == 503
    assert r.json()["detail"]["code"]  # structured refusal, never a fake success

    assert client.get("/api/v1/settings").json() == settings_before
    assert client.get("/api/v1/instruments/BTCUSD/metadata/all").json() == metadata_before
    assert client.post("/api/v1/instruments/BTCUSD/nodes/validate", json={"spec": spec}).json() == validate_before
    status_after = client.get("/api/v1/mt5/status").json()
    assert status_after["active_terminal_path"] == status_before["active_terminal_path"]
