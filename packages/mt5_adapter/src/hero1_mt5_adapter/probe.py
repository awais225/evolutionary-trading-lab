"""Read-only MT5 availability probe. Never initializes a terminal, never logs in.
"""
from __future__ import annotations

import platform
import sys


def probe_mt5_availability() -> dict:
    out = {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "meta_package_installed": False,
        "meta_package_version": None,
        "status": "unavailable",
        "detail": "",
    }
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError:
        out["detail"] = (
            "MetaTrader5 package not importable here (expected on Linux/macOS; "
            "on Windows install it with `pip install MetaTrader5` inside the venv). "
            "No connection was attempted."
        )
        return out
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"MetaTrader5 import failed: {type(exc).__name__}: {exc}"
        out["status"] = "error"
        return out

    out["meta_package_installed"] = True
    out["meta_package_version"] = getattr(mt5, "__version__", "unknown")
    if sys.platform != "win32":
        out["detail"] = (
            "Package importable but this is not Windows. Hero1 never initializes a terminal "
            "from non-Windows platforms. Status reported as 'unavailable', NOT connected."
        )
        return out
    out["status"] = "package_present"
    out["detail"] = (
        "MetaTrader5 package present on Windows. Terminal connectivity is UNVERIFIED here — "
        "run scripts/doctor.py --mt5 on the laptop with the terminal open for a real check."
    )
    return out
