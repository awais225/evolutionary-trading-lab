import sys
from pathlib import Path
import pytest
import yaml

_p = Path(__file__).resolve()
_backend = None
for parent in [_p.parent.parent, _p.parent.parent.parent]:
    candidate = parent / "backend"
    if candidate.exists():
        _backend = candidate
        break
if _backend and str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import app.config as cfgmod
from app.config import load_config
from app.db.database import Database
import app.db.database as dbmod
from app.data.engine import DataEngine


@pytest.fixture(scope="module")
def lab_env(tmp_path_factory):
    old_config_path = cfgmod.CONFIG_PATH
    old_cfg = cfgmod._config
    orig_db = dbmod._db

    tmp_path = tmp_path_factory.mktemp("lab_test")
    db_file = tmp_path / "lab_state.db"
    cache_dir = tmp_path / "data_cache"
    cfg_file = tmp_path / "lab_config.yaml"

    cfg_file.write_text(yaml.safe_dump({
        "data": {
            "symbol": "XAUUSD", "enabled_symbols": ["XAUUSD"],
            "timeframes": ["M15"], "history_months": {"M15": 1},
            "cache_dir": str(cache_dir),
        },
        "evolution": {
            "population_size": 25, "screen_batch_size": 10,
            "detail_batch_size": 5, "workers": 1, "max_indicators": 3,
        },
        "backtest": {"min_trades": 3, "screen_min_trades": 2},
        "fitness": {"min_trades": 30},
        "risk": {"kill_switch": False},
        "paper": {"autostart": False},
        "database_path": str(db_file),
        "log_level": "ERROR",
    }))

    cfg = load_config(cfg_file)
    test_db = Database(str(db_file))
    dbmod._db = test_db

    de = DataEngine()
    ingest_rep = de.ingest("XAUUSD", "M15")
    did = ingest_rep["dataset_id"]

    yield {
        "db": test_db,
        "dataset_id": did,
        "tmp_path": tmp_path,
        "config": cfg,
        "data_engine": de,
    }
    dbmod._db = orig_db
    cfgmod.CONFIG_PATH = old_config_path
    cfgmod._config = old_cfg
