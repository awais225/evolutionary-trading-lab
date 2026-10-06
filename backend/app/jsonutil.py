"""JSON helpers that tolerate numpy scalars/arrays coming from the engines."""
from __future__ import annotations

import json
from typing import Any


def np_converter(o: Any):
    try:
        import numpy as np
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            f = float(o)
            return f if f == f and abs(f) != float("inf") else None
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, np.bool_):
            return bool(o)
    except ImportError:
        pass
    if isinstance(o, float) and (o != o or abs(o) == float("inf")):
        return None
    return str(o)


def jd(obj: Any) -> str:
    """json.dumps with numpy/nan/inf tolerance (nan/inf -> null)."""
    return json.dumps(obj, default=np_converter, allow_nan=False)
