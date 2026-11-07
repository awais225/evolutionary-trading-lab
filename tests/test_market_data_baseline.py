"""Permanent baseline storage (ISSUE-049).

The supplied CSVs live permanently at DATA/MARKET_DATA/IMPORTS/<SYMBOL>/
inside the project (tracked in git); every installation imports them into its
own canonical DATA/MARKET_DATA/market_data.db. These tests pin:
  * importer discovery mode (no file args) + project data-root behaviour,
  * fast SHA-256 early-skip (no re-parse, no duplicates on re-import),
  * ensure_baseline() used by the API lifespan (imports once, then skips),
  * availability reporting of mt5_fetch_required against stored candles.

All writes go to pytest tmp_path - the real DATA root is never touched.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

from app import market_data_service as svc

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "import_market_data.py"
_spec = importlib.util.spec_from_file_location("import_market_data", _SCRIPT)
imp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(imp)

HEADER = ["<DATE>", "<TIME>", "<OPEN>", "<HIGH>", "<LOW>", "<CLOSE>",
          "<TICKVOL>", "<VOL>", "<SPREAD>"]


def _write_csv(path: Path, rows: int = 3) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(HEADER)]
    for i in range(rows):
        lines.append("\t".join(["2020.10.01", f"{1 + i:02d}:00:00", "1900", "1902",
                                "1899", "1901", "100", "0", "12"]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _counts(db: Path) -> int:
    return sqlite3.connect(db).execute("SELECT COUNT(*) FROM candles").fetchone()[0]


def test_importer_discovery_imports_permanent_baseline(tmp_path):
    data_root = tmp_path / "DATA"
    _write_csv(data_root / "MARKET_DATA" / "IMPORTS" / "XAUUSD" / "XAUUSD_H1.csv")
    rc = imp.main(["--data-root", str(data_root)])
    assert rc == 0
    db = data_root / "MARKET_DATA" / "market_data.db"
    assert _counts(db) == 3
    # re-run: every file SKIPPED by SHA-256, still exactly 3 candles
    rc = imp.main(["--data-root", str(data_root)])
    assert rc == 0
    assert _counts(db) == 3


def test_importer_discovery_without_csvs_is_a_clear_error(tmp_path, capsys):
    rc = imp.main(["--data-root", str(tmp_path / "DATA")])
    assert rc == 2
    assert "no permanent baseline CSVs" in capsys.readouterr().err


def test_early_skip_does_not_reparse(tmp_path):
    csv_path = _write_csv(tmp_path / "XAUUSD_H1.csv")
    db = tmp_path / "DATA" / "MARKET_DATA" / "market_data.db"
    first = imp.import_file(csv_path, instrument="XAUUSD", timeframe="H1",
                            db_path=db, force=False, dry_run=False)
    assert first["status"] == "AVAILABLE" and first["row_count"] == 3
    second = imp.import_file(csv_path, instrument="XAUUSD", timeframe="H1",
                             db_path=db, force=False, dry_run=False)
    assert second["action"].startswith("SKIPPED")
    assert second["row_count"] == 3
    assert _counts(db) == 3


def test_ensure_baseline_and_availability_fetch_required(tmp_path, monkeypatch):
    db = tmp_path / "DATA" / "MARKET_DATA" / "market_data.db"
    _write_csv(tmp_path / "DATA" / "MARKET_DATA" / "IMPORTS" / "XAUUSD" / "XAUUSD_H1.csv")
    monkeypatch.setattr(svc, "store_db_path", lambda: db)

    report = svc.ensure_baseline()
    assert report["imported"] == 1 and report["skipped"] == 0 and "error" not in report
    again = svc.ensure_baseline()
    assert again["skipped"] == 1 and _counts(db) == 3

    view = svc.availability("XAUUSD")
    assert view["store"] == str(db)
    h1 = view["timeframes"]["H1"]
    assert h1["status"] == "AVAILABLE" and h1["row_count"] == 3
    # latest stored candle is far in the past -> a newer completed candle is due
    assert h1["mt5_fetch_required"] is True
    assert view["timeframes"]["M1"]["baseline_missing"] is True
    assert view["summary"]["last_successful_import"] is not None
    assert view["summary"]["last_mt5_update"] is None
    assert "H1" in view["summary"]["mt5_fetch_required_timeframes"]


def test_ensure_baseline_without_csvs_reports_reason(tmp_path, monkeypatch):
    db = tmp_path / "DATA" / "MARKET_DATA" / "market_data.db"
    monkeypatch.setattr(svc, "store_db_path", lambda: db)
    report = svc.ensure_baseline()
    assert report["imported"] == 0 and "no permanent baseline CSVs" in report["reason"]
    assert not db.exists()
