"""
JSON response safety net (V4.7).

The dashboard must never receive invalid JSON, ``NaN``/``Infinity`` tokens, or a
raw Python object rendered as ``[object Object]``. Individual endpoints already
clean their payloads (``app.jsonutil.jd``), but a single missed value anywhere
would previously either produce a 500 from the renderer (Starlette serialises
with ``allow_nan=False``) or an unusable response.

``SafeJSONResponse`` is installed as the application default response class and
recursively sanitises the payload immediately before serialisation:

  * non-finite floats (NaN, ±Inf)          -> ``None`` (rendered as N/A by the UI)
  * ``Path`` / ``Decimal`` / date/time     -> string
  * dataclasses / objects with ``to_dict`` -> their dictionary form
  * any other unknown object               -> ``str(obj)`` (never "[object Object]")

It never invents values: anything unknown becomes ``None`` or its string form,
which the frontend already treats as unavailable.
"""
from __future__ import annotations

import dataclasses
import json
import datetime as _dt
import decimal
import logging
import math
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse

log = logging.getLogger("api.json_safety")

#: recursion guard - deeper structures are stringified instead of walked
MAX_DEPTH = 12
#: short marker used when a value cannot be represented
UNREPRESENTABLE = "<unrepresentable>"


def sanitize_payload(obj: Any, _depth: int = 0) -> Any:
    """Return a JSON-safe copy of ``obj`` (never raises)."""
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (bytes, bytearray)):
        try:
            return obj.decode("utf-8", errors="replace")
        except Exception:
            return UNREPRESENTABLE
    if _depth >= MAX_DEPTH:
        return _safe_str(obj)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = k if isinstance(k, str) else _safe_str(k)
            out[key] = sanitize_payload(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [sanitize_payload(v, _depth + 1) for v in obj]
    if isinstance(obj, decimal.Decimal):
        try:
            f = float(obj)
            return f if math.isfinite(f) else None
        except Exception:
            return None
    if isinstance(obj, (Path,)):
        return str(obj)
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        try:
            return sanitize_payload(dataclasses.asdict(obj), _depth + 1)
        except Exception:
            return _safe_str(obj)
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            return sanitize_payload(to_dict(), _depth + 1)
        except Exception:
            return _safe_str(obj)
    return _safe_str(obj)


def _safe_str(obj: Any) -> str:
    try:
        s = str(obj)
    except Exception:
        return UNREPRESENTABLE
    return s if s else UNREPRESENTABLE


class SafeJSONResponse(JSONResponse):
    """JSONResponse that can always be serialised, whatever a handler returns.

    V4.7 note on cost: the sanitiser walks (and copies) the whole payload, so
    running it on every response was measurable - the dashboard endpoints paid
    ~10-20% extra latency for it even though their payloads were already clean.
    The response is therefore serialised first, exactly as Starlette would
    (``allow_nan=False``, same separators), and the sanitiser only runs when
    that serialisation actually fails. A clean payload costs nothing extra; a
    payload containing NaN/Infinity or a non-JSON object is sanitised and
    re-serialised instead of becoming a 500 or invalid JSON.
    """

    def render(self, content: Any) -> bytes:
        try:
            return json.dumps(content, ensure_ascii=False, allow_nan=False,
                              indent=None, separators=(",", ":")).encode("utf-8")
        except (ValueError, TypeError) as e:
            first_error = e
        try:
            clean = sanitize_payload(content)
        except Exception as e:  # a sanitiser bug must not take the API down
            log.warning("payload sanitisation failed for %s: %s", type(first_error).__name__, e)
            clean = {"error": "response sanitisation failed", "detail": str(first_error)}
        try:
            return super().render(clean)
        except (ValueError, TypeError) as e:
            # last resort: never return a 500 because of one odd value
            log.warning("JSON serialisation failed after sanitisation: %s", e)
            return super().render({"error": "response not serialisable", "detail": str(e)})
