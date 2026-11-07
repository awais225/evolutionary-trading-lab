"""The repository must never contain DATA, databases, logs, or secrets.
Checks TRACKED files via `git ls-files` — build artifacts (node_modules/, dist/)
are git-ignored and therefore irrelevant here.
"""
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3",
                      ".parquet", ".gguf", ".safetensors", ".log")


def _tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT,
                         capture_output=True, text=False, check=True).stdout
    return [REPO_ROOT / p for p in out.decode().split("\0") if p]


def test_no_forbidden_files_tracked():
    offenders = [str(p) for p in _tracked_files()
                 if p.suffix in FORBIDDEN_SUFFIXES or p.name == ".env"]
    assert offenders == [], f"forbidden tracked files: {offenders}"


def _approved_csv_import(p: Path) -> bool:
    """The ONLY tracked DATA files allowed: the approved historical source
    CSVs under DATA/MARKET_DATA/IMPORTS/<SYMBOL>/*.csv (owner directive
    2026-11-07). Databases, manifests and all other DATA stay forbidden."""
    parts = p.parts
    return (len(parts) >= 5 and parts[-5] == "DATA" and parts[-4] == "MARKET_DATA"
            and parts[-3] == "IMPORTS" and p.suffix == ".csv")


def test_no_data_directories_tracked():
    offenders = [str(p) for p in _tracked_files()
                 if ("DATA" in p.parts or "node_modules" in p.parts)
                 and not _approved_csv_import(p)]
    assert offenders == [], f"forbidden tracked paths: {offenders}"


def test_approved_csv_imports_are_tracked():
    tracked = [p for p in _tracked_files() if _approved_csv_import(p)]
    names = sorted(p.name for p in tracked)
    assert names == sorted(["XAUUSD_Daily.csv", "XAUUSD_H1.csv", "XAUUSD_H4.csv",
                            "XAUUSD_M1.csv", "XAUUSD_M5.csv", "XAUUSD_M15.csv",
                            "XAUUSD_M30.csv"]), f"approved XAUUSD CSVs tracked: {names}"


def test_gitignore_hard_rules():
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for needle in ("DATA/", ".env", "*.db", "logs/", "node_modules/"):
        assert needle in text


def test_env_example_has_no_secrets():
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").lower()
    for needle in ("github_pat_", "ghp_", "sk-", "password", "secret_key"):
        assert needle not in text
