"""Server-side broker metadata store (per broker/server + symbol).

Layout inside the DATA root:

    <DATA_ROOT>/MARKET_METADATA/<server-slug>/<SYMBOL>.json     # snapshot
    <DATA_ROOT>/MARKET_METADATA/manifest.json                    # index + sha256

Rules:
  * snapshots are written atomically (tmp + os.replace) and the manifest is
    rebuilt on every write; readers tolerate a missing/corrupt manifest;
  * one broker's metadata can never overwrite another's: the server slug is
    part of the path; same server+symbol simply replaces with the newer
    snapshot (its fingerprint is recorded in the manifest);
  * account login is stored as CONTEXT only (never credentials);
  * read-only queries never create the directory.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hero1_storage.engine import data_root

_MANIFEST = "manifest.json"
_SLUG_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def meta_root() -> Path:
    return data_root() / "MARKET_METADATA"


def _safe_slug(server: str) -> str:
    slug = _SLUG_RE.sub("_", str(server or "").strip())[:96].strip("._")
    return slug or "unknown-server"


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot_path(server: str, symbol: str) -> Path:
    return meta_root() / _safe_slug(server) / f"{symbol.strip().upper()}.json"


def _rebuild_manifest() -> Dict[str, Any]:
    root = meta_root()
    entries: List[Dict[str, Any]] = []
    if root.is_dir():
        for server_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            for file in sorted(server_dir.glob("*.json")):
                try:
                    data = json.loads(file.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001 - one bad file must not kill the index
                    continue
                entries.append({
                    "instrument": data.get("instrument"),
                    "broker_server": data.get("broker_server"),
                    "source": data.get("source"),
                    "retrieved_at": data.get("retrieved_at"),
                    "fingerprint": data.get("fingerprint"),
                    "terminal": data.get("terminal"),
                    "account_login": data.get("account_login"),
                    "sha256": _sha256_file(file),
                    "path": str(file),
                    "validation_ok": bool((data.get("validation") or {}).get("ok")),
                })
    manifest = {"schema_version": 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "snapshots": entries}
    _atomic_write_json(root / _MANIFEST, manifest)
    return manifest


def save_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    server = str(snapshot.get("broker_server") or "").strip()
    symbol = str(snapshot.get("instrument") or "").strip().upper()
    if not server or not symbol:
        raise ValueError("snapshot needs broker_server and instrument")
    path = _snapshot_path(server, symbol)
    _atomic_write_json(path, snapshot)
    manifest = _rebuild_manifest()
    sha = _sha256_file(path)
    return {"saved": True, "path": str(path), "sha256": sha,
            "manifest_entries": len(manifest["snapshots"]),
            "fingerprint": snapshot.get("fingerprint")}


def load_manifest() -> Dict[str, Any]:
    path = meta_root() / _MANIFEST
    if not path.is_file():
        return {"schema_version": 1, "updated_at": None, "snapshots": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("snapshots"), list):
            raise ValueError("manifest shape invalid")
        return data
    except Exception:  # noqa: BLE001
        return {"schema_version": 1, "updated_at": None,
                "snapshots": [], "manifest_error": f"{path} is unreadable; rebuild by re-importing metadata"}


def list_snapshots(instrument: Optional[str] = None) -> List[Dict[str, Any]]:
    snaps = load_manifest()["snapshots"]
    if instrument:
        want = instrument.strip().upper()
        snaps = [s for s in snaps if str(s.get("instrument") or "").upper() == want]
    return sorted(snaps, key=lambda s: str(s.get("retrieved_at") or ""), reverse=True)


def best_snapshot(instrument: str) -> Optional[Dict[str, Any]]:
    want = instrument.strip().upper()
    candidates = []
    root = meta_root()
    if not root.is_dir():
        return None
    for server_dir in root.iterdir():
        if not server_dir.is_dir():
            continue
        file = server_dir / f"{want}.json"
        if not file.is_file():
            continue
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if str(data.get("instrument") or "").upper() == want:
            data["_stored_path"] = str(file)
            candidates.append(data)
    if not candidates:
        return None
    candidates.sort(key=lambda s: str(s.get("retrieved_at") or ""), reverse=True)
    return candidates[0]


def snapshot_broker_meta(instrument: str) -> Optional[Dict[str, Dict[str, float]]]:
    """Validation contract: {SYMBOL: {point_size, tick_size, pip_size, contract_size}}."""
    snap = best_snapshot(instrument)
    if not snap:
        return None
    from hero1_mt5_adapter.metadata import broker_meta_from_snapshot
    meta = broker_meta_from_snapshot(snap)
    return meta or None


def available_view(instrument: str) -> Dict[str, Any]:
    want = instrument.strip().upper()
    snaps = list_snapshots(want)
    best = best_snapshot(want)
    out: Dict[str, Any] = {
        "instrument": want,
        "available": best is not None,
        "snapshot_count": len(snaps),
        "metadata_root": str(meta_root()),
        "snapshots": snaps,
    }
    if best is not None:
        fields = best.get("fields") or {}
        out["snapshot"] = {
            "broker_server": best.get("broker_server"),
            "terminal": best.get("terminal"),
            "account_login": best.get("account_login"),
            "source": best.get("source"),
            "retrieved_at": best.get("retrieved_at"),
            "fingerprint": best.get("fingerprint"),
            "digits": fields.get("digits"),
            "derived": best.get("derived"),
            "validation": best.get("validation"),
            "tick": best.get("tick"),
            "stops_level": fields.get("stops_level"),
            "freeze_level": fields.get("freeze_level"),
            "volume_min": fields.get("volume_min"),
            "volume_max": fields.get("volume_max"),
            "volume_step": fields.get("volume_step"),
            "currency_base": fields.get("currency_base"),
            "currency_profit": fields.get("currency_profit"),
            "currency_margin": fields.get("currency_margin"),
            "trade_mode": fields.get("trade_mode"),
        }
    else:
        out["reason"] = ("no stored MT5 metadata for this instrument. Metadata cannot be guessed: "
                         "connect MT5 (top bar) and press Refresh, or POST the broker's symbol_info "
                         "to /api/v1/instruments/%s/metadata/import." % want)
    return out


__all__ = ["meta_root", "save_snapshot", "load_manifest", "list_snapshots",
           "best_snapshot", "snapshot_broker_meta", "available_view"]
