"""V6.4 #2 — normalize the REAL MT5 order_send result; never label a DONE unknown.

Defect: the broker returned retcode 10009 with deal/order tickets and a fill
price, but ``_result_to_dict`` enumerated ``_fields``/``__dict__`` — empty for
``__slots__``/dict-like/foreign result objects — so the executed trade surfaced
as UNKNOWN (ORDER_SEND_UNUSIFIED_RESULT / NO_USABLE_RESULT).

Fix at the narrowest point: the result reader reads the actual MT5 fields
directly (retcode, deal, order, volume, price, comment, request_id and
extensions), preserves a readable retcode even when it is a numpy-style int,
keeps raw diagnostics for odd types, and success is 10009 ONLY with the result
confirming execution. No order is ever sent in these tests.
"""
from __future__ import annotations

import collections

import pytest

from app.mt5.mt5_real import _result_to_dict, _RESULT_FIELDS, OrderResult, MT5RealBridge
from app.mt5.execution import interpret_retcode, _usable_retcode


# --------------------------------------------------------------------------- #
# real-ish result shapes
# --------------------------------------------------------------------------- #
_RealResult = collections.namedtuple(
    "_RealResult", "retcode deal order volume price bid ask comment request_id retcode_external")


class _SlotsResult:
    __slots__ = ("retcode", "deal", "order", "volume", "price", "comment", "request_id")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _PropResult:
    """A foreign object exposing properties (no _fields, no __dict__)."""
    def __init__(self, **kw):
        self._data = kw

    @property
    def retcode(self): return self._data.get("retcode")
    @property
    def deal(self): return self._data.get("deal")
    @property
    def order(self): return self._data.get("order")
    @property
    def volume(self): return self._data.get("volume")
    @property
    def price(self): return self._data.get("price")
    @property
    def comment(self): return self._data.get("comment")
    @property
    def request_id(self): return self._data.get("request_id")


class _NumpyInt:
    """A numpy-integer style retcode (not a Python int instance)."""
    def __init__(self, v): self._v = int(v)
    def __int__(self): return self._v
    def __index__(self): return self._v
    def __eq__(self, other): return int(self._v) == other
    def __hash__(self): return hash(self._v)
    def __repr__(self): return f"int64({self._v})"


def test_01_namedtuple_result_reads_every_known_field():
    r = _RealResult(retcode=10009, deal=555001, order=777001, volume=0.02,
                    price=2400.35, bid=2400.3, ask=2400.4, comment="done",
                    request_id=9, retcode_external=0)
    raw = _result_to_dict(r)
    assert raw["retcode"] == 10009
    assert raw["deal"] == 555001 and raw["order"] == 777001
    assert raw["volume"] == pytest.approx(0.02) and raw["price"] == pytest.approx(2400.35)
    assert raw["comment"] == "done" and raw["request_id"] == 9


def test_02_dict_result_is_preserved_verbatim():
    raw = _result_to_dict({"retcode": 10009, "deal": 1, "order": 2, "comment": "ok"})
    assert raw["retcode"] == 10009 and raw["deal"] == 1


def test_03_slots_object_reads_the_same_fields():
    r = _SlotsResult(retcode=10009, deal=555002, order=777002, volume=0.1,
                     price=4190.79, comment="done", request_id=11)
    raw = _result_to_dict(r)
    assert raw["retcode"] == 10009
    assert raw["deal"] == 555002 and raw["order"] == 777002
    assert raw["price"] == pytest.approx(4190.79)
    assert raw["comment"] == "done" and raw["request_id"] == 11


def test_04_property_object_reads_the_same_fields():
    r = _PropResult(retcode=10009, deal=555003, order=777003, volume=0.5,
                    price=4190.5, comment="done", request_id=12)
    raw = _result_to_dict(r)
    assert raw["retcode"] == 10009 and raw["deal"] == 555003


def test_05_numpy_style_retcode_is_coerced_to_int():
    r = _SlotsResult(retcode=_NumpyInt(10009), deal=1, order=2, comment="done")
    raw = _result_to_dict(r)
    assert isinstance(raw["retcode"], int) and not isinstance(raw["retcode"], bool)
    assert raw["retcode"] == 10009
    assert _usable_retcode(raw["retcode"]) is True
    assert interpret_retcode(raw["retcode"])["category"] == "EXECUTED"


def test_06_extension_fields_survive():
    class _Extended(_SlotsResult):
        __slots__ = ("retcode", "deal", "order", "volume", "price", "comment",
                     "request_id", "position_id", "external_id")

    r = _Extended(retcode=10009, deal=1, order=2, comment="done", request_id=3,
                  position_id=42, external_id="ext-1")
    raw = _result_to_dict(r)
    assert raw["position_id"] == 42 and raw["external_id"] == "ext-1"


def test_07_none_and_unreadable_objects_keep_raw_diagnostics():
    assert _result_to_dict(None) == {}
    class _Weird:
        def __repr__(self): return "<Weird result 0xdead>"

    raw = _result_to_dict(_Weird())
    assert raw.get("_repr") == "<Weird result 0xdead>"
    assert raw.get("_type") == "_Weird"

    class _BrokenRepr:
        def __repr__(self): raise RuntimeError("repr exploded")

    raw2 = _result_to_dict(_BrokenRepr())
    assert "repr failed" in raw2.get("_repr", "")


def test_08_a_readable_object_is_never_reported_unreadable():
    """The bug: any of these shapes used to come back as {} -> UNKNOWN."""
    for r in (_SlotsResult(retcode=10009, deal=1, order=2, comment="done"),
              _PropResult(retcode=10009, deal=1, order=2, comment="done"),
              {"retcode": 10009, "deal": 1, "order": 2, "comment": "done"}):
        raw = _result_to_dict(r)
        assert raw.get("retcode") == 10009, raw


# --------------------------------------------------------------------------- #
# OrderResult success semantics — 10009 with confirmation, never merely "a result"
# --------------------------------------------------------------------------- #
def test_09_place_order_success_requires_done_confirmation(monkeypatch):
    """place_order returns ok=True ONLY for retcode 10009 (V6.4 narrow fix)."""
    import types
    from app.mt5 import mt5_real

    class _ChainBridge(MT5RealBridge):
        def __init__(self):
            self._result = None
            self.source = "MT5"
            self._connected = True

        def _send_with_filling_fallback(self, request, mode_sequence):
            return {"result": self._result, "raised": None,
                    "request": dict(request, type_filling=1),
                    "attempts": [{"type_filling": 1}], "last_error_after": None}

        def latest_tick(self, symbol):
            return types.SimpleNamespace(bid=2400.0, ask=2400.3, ts=0.0)

        def symbol_info(self, symbol):
            return types.SimpleNamespace(digits=2, point=0.01)

    monkeypatch.setattr(mt5_real, "MT5_PACKAGE_AVAILABLE", True)
    b = _ChainBridge()

    b._result = _SlotsResult(retcode=10009, deal=9, order=8, volume=0.01,
                             price=2400.3, comment="done")
    res = MT5RealBridge.place_order(b, symbol="XAUUSD", side="BUY", lots=0.01)
    assert res.ok is True and res.retcode == 10009
    assert res.deal_id == 9 and res.order_id == 8

    # a readable but rejected retcode is NOT success — it travels verbatim
    b._result = _SlotsResult(retcode=10016, deal=0, order=0, comment="invalid stops")
    res2 = MT5RealBridge.place_order(b, symbol="XAUUSD", side="BUY", lots=0.01)
    assert res2.ok is False and res2.retcode == 10016
    assert "invalid stops" in res2.comment

    # a result object with no readable retcode is still not success
    b._result = object()
    res3 = MT5RealBridge.place_order(b, symbol="XAUUSD", side="BUY", lots=0.01)
    assert res3.ok is False


def test_10_interpret_retcode_never_infers_from_a_connection_message():
    assert interpret_retcode(10009)["category"] == "EXECUTED"
    assert interpret_retcode(10009)["safe_to_retry"] is False   # executed is not a retry
    assert interpret_retcode(10016)["category"] == "REJECTED"
    assert interpret_retcode(10016)["safe_to_retry"] is True
    assert interpret_retcode(None)["category"] == "UNKNOWN"
    assert interpret_retcode(None)["safe_to_retry"] is False
    assert interpret_retcode(10031)["category"] == "TIMEOUT"
    assert interpret_retcode(10031)["safe_to_retry"] is False
    unknown = interpret_retcode(123456)
    assert unknown["category"] == "UNKNOWN" and unknown["safe_to_retry"] is False


def test_11_result_field_list_covers_the_broker_facts():
    for f in ("retcode", "deal", "order", "volume", "price", "comment", "request_id"):
        assert f in _RESULT_FIELDS
