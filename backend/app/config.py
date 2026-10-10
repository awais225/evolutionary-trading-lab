"""
Central configuration for the Evolutionary Trading Research Lab (V2).

All tunables live here and can be edited at runtime from the dashboard
(Settings page). Values are persisted to CONFIG/lab_config.yaml (V1 files in
config/ are migrated automatically — see app/paths.py).

V2 additions: ResourcesConfig (CPU/memory/GPU limits, spec §26-32),
ResearchConfig (legacy-result policy, re-evaluation policy, export, activity
retention), AppearanceConfig (theme + tree colors, spec §36/39), MT5
connection_mode (spec §14), paper.autostart safety default (spec §49), and
portable relative-path persistence (paths under the app root are saved
relative, so the whole folder can be moved between drives/machines).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List

import yaml

from . import paths as P

ROOT_DIR = P.ROOT_DIR
CONFIG_PATH = P.config_file()


@dataclass
class MT5Config:
    # mode: "auto" (real MT5 if available else labelled SIMULATOR) |
    #       "real" (never simulate) | "simulator" (explicitly synthetic)
    mode: str = "auto"
    # connection_mode (spec §14):
    #   "auto"              -> initialize() attaches to the running terminal
    #   "existing"          -> same as auto (use the already-authenticated session)
    #   "path"              -> initialize(path=<terminal64.exe>)
    connection_mode: str = "auto"
    login: int = 0
    password: str = ""            # NEVER logged; only used for explicit login
    server: str = ""
    path: str = ""                # terminal64.exe path (connection_mode="path")
    timeout_ms: int = 60000
    reconnect_min_s: int = 5      # monitor retry backoff bounds (spec §19)
    reconnect_max_s: int = 60
    feed_stale_s: int = 120       # no fresh tick for this long => DATA FEED STALE


@dataclass
class DataConfig:
    symbol: str = "XAUUSD"
    enabled_symbols: List[str] = field(default_factory=lambda: ["XAUUSD"])
    future_symbols: List[str] = field(default_factory=lambda: ["BTCUSD", "NAS100", "EURUSD"])
    timeframes: List[str] = field(default_factory=lambda: ["M1", "M5", "M15", "M30", "H1"])
    # Months of history per timeframe used for the INITIAL master bootstrap
    # (M1 kept short to bound memory/disk). Incremental syncs only fetch the
    # range missing from the master store (spec §4).
    history_months: Dict[str, float] = field(default_factory=lambda: {
        "M1": 1, "M5": 6, "M15": 12, "M30": 12, "H1": 12,
    })
    data_root: str = "DATA"            # V2 master store root (relative ok)
    cache_dir: str = "data_cache"      # V1 legacy snapshot dir (kept readable)
    integrity_quarantine: bool = True  # flag+quarantine bad rows, never silent


@dataclass
class EvolutionConfig:
    population_size: int = 1000
    total_node_target: int = 500     # user defined global total node ceiling (spec §V2.1 D)
    elite_pct: float = 0.10
    mutation_pct: float = 0.50
    crossover_pct: float = 0.30
    exploration_pct: float = 0.10
    max_indicators: int = 3          # early generations complexity cap
    max_indicators_hard: int = 8     # complexity may grow, with penalty
    max_conditions: int = 6
    max_condition_depth: int = 3
    simplify_probability: float = 0.15
    mutation_rate: float = 0.35      # per-gene probability inside param mutation
    tournament_size: int = 6
    species_cap_pct: float = 0.25    # max share of elites from one species
    # orchestrator pacing
    screen_batch_size: int = 40      # stage-1 backtests per loop tick
    detail_batch_size: int = 10      # stage-2 backtests per loop tick
    validation_batch_size: int = 3
    workers: int = max(1, (os.cpu_count() or 2))
    loop_interval_s: float = 0.5


@dataclass
class BacktestConfig:
    initial_balance: float = 10_000.0
    risk_per_trade: float = 0.005        # 0.5% equity risked per trade
    commission_per_lot: float = 7.0      # USD round turn
    swap_per_lot_per_day: float = -2.5   # USD, negative = cost
    contract_size: float = 100.0         # XAUUSD: 100 oz per lot
    max_concurrent_positions: int = 1
    default_spread_points: float = 18.0
    point_value: float = 0.01
    slippage_model: str = "normal"       # normal | uniform | empirical
    slippage_mean_points: float = 0.8
    slippage_std_points: float = 0.6
    slippage_max_points: float = 5.0
    execution_delay_ms: int = 120
    min_hold_bars: int = 1
    max_hold_bars: int = 96
    min_trade_duration_seconds: int = 120 # Default: 2 minutes (spec §7)
    train_fraction: float = 0.7          # first 70% in-sample
    min_trades: int = 30
    screen_max_hold_bars: int = 48
    screen_min_trades: int = 10


@dataclass
class FitnessConfig:
    weights: Dict[str, float] = field(default_factory=lambda: {
        "profitability": 0.20,
        "risk_adjusted": 0.20,
        "drawdown": 0.15,
        "profit_factor": 0.15,
        "consistency": 0.10,
        "oos": 0.10,
        "robustness": 0.05,
        "complexity": 0.05,
    })
    complexity_penalty_per_indicator: float = 0.02
    complexity_penalty_per_condition: float = 0.01
    max_drawdown_pct: float = 0.30
    min_profit_factor: float = 1.05
    min_trades: int = 30
    min_sharpe: float = 0.15
    max_stress_degradation: float = 0.60
    max_perturbation_instability: float = 0.55
    oos_degradation_limit: float = 0.65


@dataclass
class RiskConfig:
    max_daily_drawdown_pct: float = 0.05
    max_strategy_drawdown_pct: float = 0.15
    max_spread_points: float = 45.0
    max_position_size_lots: float = 2.0
    max_concurrent_positions: int = 3
    max_trades_per_minute: int = 6
    min_holding_seconds: int = 30
    max_holding_seconds: int = 86400 * 3
    max_allowed_slippage_points: float = 10.0
    allowed_sessions: List[str] = field(default_factory=lambda: ["asia", "london", "newyork"])
    kill_switch: bool = False            # PERSISTED across restarts (spec §33)
    real_execution_enabled: bool = False  # NEVER auto-enabled

    # V3 Paper Hard Capital Risk Controls (spec §22)
    starting_capital: float = 10000.0
    max_risk_per_trade_pct: float = 0.01   # 1.0%
    max_risk_per_trade_abs: float = 100.0  # $100.0
    max_daily_loss_pct: float = 0.03       # 3.0%
    max_daily_loss_abs: float = 300.0      # $300.0
    max_total_drawdown_pct: float = 0.10   # 10.0%
    max_exposure_pct: float = 0.30         # 30.0%


@dataclass
class PaperConfig:
    enabled_strategies_max: int = 8
    tick_interval_ms: int = 400
    latency_ms_mean: int = 110
    latency_ms_std: int = 40
    slippage_mean_points: float = 1.0
    slippage_std_points: float = 0.8
    divergence_alert_threshold: float = 0.5
    autostart: bool = False       # spec §49: OFF until explicitly enabled

    # V3 Paper Trading Hard Capital Risk Controls (spec §22)
    starting_capital: float = 10000.0
    max_risk_per_trade_pct: float = 0.01   # 1.0%
    max_risk_per_trade_abs: float = 100.0  # $100.0
    max_daily_loss_pct: float = 0.03       # 3.0%
    max_daily_loss_abs: float = 300.0      # $300.0
    max_total_drawdown_pct: float = 0.10   # 10.0%
    max_concurrent_positions: int = 3
    max_exposure_pct: float = 0.30         # 30.0%


@dataclass
class AIConfig:
    researcher_enabled: bool = True
    llm_provider: str = "none"
    llm_endpoint: str = ""
    llm_model: str = ""
    llm_api_key_env: str = "LAB_LLM_API_KEY"
    max_hypotheses_per_cycle: int = 4


@dataclass
class ResourcesConfig:
    """Actual enforcement knobs (spec §26-32, V2.7) — consumed by the orchestrator
    pool sizing, the batch memory gate and the GPU init guard."""
    cpu_limit_enabled: bool = True
    cpu_target_pct: int = 60            # 10% to 100% in 10% steps (default 60%)
    cpu_workers_max: int = 0            # 0 = auto: dynamically calculated from cpu_target_pct
    memory_limit_enabled: bool = True
    memory_limit_mode: str = "pct"      # "pct" of total RAM | "gb" absolute
    memory_limit_value: float = 50.0
    gpu_enabled: bool = False           # spec §29/49: OFF initially (toggle on Dashboard/Settings)
    gpu_workload_limit_pct: int = 50    # concurrency cap for GPU workloads


@dataclass
class ResearchConfig:
    legacy_result_policy: str = "reuse"        # reuse | revalidate  (spec §45)
    reevaluate_on_data_growth: str = "qualified_only"  # none | qualified_only | all
    export_enabled: bool = True                # mirror results into RESEARCH/
    activity_retention: int = 5000             # rows kept in SQLite (rotation)
    backup_keep: int = 10                      # newest backups kept


@dataclass
class AppearanceConfig:
    theme: str = "dark"                 # dark | light | system
    status_colors: Dict[str, str] = field(default_factory=lambda: {
        "BORN": "#64748b", "BACKTESTING": "#0ea5e9", "SURVIVED": "#22c55e",
        "VALIDATING": "#a78bfa", "QUALIFIED": "#16a34a", "PAPER": "#f59e0b",
        "FAILED": "#ef4444", "KILLED": "#dc2626", "RETIRED": "#717171",
    })
    edge_color: str = "#3b4658"
    edge_width: float = 1.4
    text_color: str = "#d5dbe8"
    node_selected: str = "#facc15"
    node_hover: str = "#38bdf8"
    active_highlight: str = "#22c55e"


@dataclass
class LiveTestingConfig:
    """V4.3 - controlled live (demo) testing limits.

    Live Testing itself always starts INACTIVE: there is deliberately NO
    "enabled" flag here (and none in the database) so no restart, reconnect or
    reload can ever resume trading by itself. These are only the boundaries the
    operator-configured activation is validated against.
    """
    risk_pct_default: float = 1.0      # % of account equity risked per trade
    risk_pct_max: float = 2.0          # hard ceiling; above this a trade is BLOCKED
    # V6.5.1 §8 — Mode B (constant monetary risk) ceiling, account currency. A
    # configured per-trade money risk above this is refused, never clamped.
    risk_amount_max: float = 100000.0
    max_active_trades: int = 1         # conservative start
    # V6.5 §5 — the DEFAULT per-node cap (each node inherits it unless the node
    # carries an explicit override in live_test_configs.max_positions).
    # Conservative default = 1; changing this default only affects nodes that
    # inherit it, never nodes with their own explicit limit.
    max_active_trades_per_node_default: int = 1
    tick_interval_s: float = 15.0      # live-testing evaluation interval
    max_data_age_s: int = 120          # stale market data blocks new trades
    require_sl: bool = True            # risk-based sizing needs a real SL
    # V5 §13 — manual order panel defaults (all editable in the panel)
    manual_sl_pips_default: float = 300.0      # default stop distance, pips
    manual_risk_amount_default: float = 10.0   # default money at risk, account currency


@dataclass
class LabConfig:
    mt5: MT5Config = field(default_factory=MT5Config)
    data: DataConfig = field(default_factory=DataConfig)
    evolution: EvolutionConfig = field(default_factory=EvolutionConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    fitness: FitnessConfig = field(default_factory=FitnessConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    paper: PaperConfig = field(default_factory=PaperConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    resources: ResourcesConfig = field(default_factory=ResourcesConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    live_testing: LiveTestingConfig = field(default_factory=LiveTestingConfig)
    appearance: AppearanceConfig = field(default_factory=AppearanceConfig)
    database_path: str = "DATABASE/lab_state.db"
    log_level: str = "INFO"


SECTIONS = (MT5Config, DataConfig, EvolutionConfig, BacktestConfig, FitnessConfig,
            RiskConfig, PaperConfig, AIConfig, ResourcesConfig, ResearchConfig,
            AppearanceConfig, LiveTestingConfig)
SECTION_NAMES = ("mt5", "data", "evolution", "backtest", "fitness", "risk",
                 "paper", "ai", "resources", "research", "appearance", "live_testing")

_lock = threading.RLock()
_config: LabConfig | None = None


def _from_dict(cls, data: Dict[str, Any]):
    fields = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
    return cls(**{k: v for k, v in (data or {}).items() if k in fields})


def _resolve(p: str) -> str:
    """Relative paths resolve against the app root (portability, spec §25)."""
    pp = Path(p)
    return str(pp if pp.is_absolute() else (ROOT_DIR / pp))


def _relativize(p: str) -> str:
    """Persist paths under ROOT_DIR as relative (folder stays movable)."""
    try:
        return str(Path(p).resolve().relative_to(ROOT_DIR)).replace(os.sep, "/")
    except (ValueError, OSError):
        return p


def _data_tree_suffix(ap: Path) -> Path | None:
    """Path below the last ``DATA`` component of `ap`, else None (V4.1)."""
    parts = ap.parts
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].upper() == "DATA":
            return Path(*parts[i + 1:])
    return None


def _resolve_data_tree(p: str) -> str:
    """Resolve a DATA-tree path (data_root / cache_dir / database_path).

    The authoritative DATA tree is ``app.paths.DATA_ROOT``, which follows the
    ``EVOLUTIONARY_LAB_DATA_ROOT`` environment override. Any configured path
    that points inside the DATA tree is therefore re-rooted onto DATA_ROOT, so
    the persistent research state can live outside the application folder
    without editing any source file (V4 LMSArena data separation).

    Paths that do NOT live inside the DATA tree keep the previous behaviour
    (relative -> app root), so an existing local installation resolves exactly
    as before and nothing is ever silently moved.

    V4.1 safety fix: when the DATA root is selected explicitly through
    ``EVOLUTIONARY_LAB_DATA_ROOT`` (P.DATA_ROOT_EXPLICIT), a persisted absolute
    path that still points into *some* DATA tree is re-rooted onto that override
    as well. Without this, a stale absolute path written into CONFIG by an
    earlier run silently redirected research writes - including the destructive
    START NEW RESEARCH RUN operations - to the production DATA tree while every
    diagnostic reported the overridden root. When the environment override is
    not used, the previous behaviour is untouched.
    """
    pp = Path(p)
    root_data = (ROOT_DIR / "DATA")
    if pp.is_absolute():
        ap = pp.resolve()
        try:
            rel = ap.relative_to(root_data.resolve())
        except (ValueError, OSError):
            rel = _data_tree_suffix(ap) if P.DATA_ROOT_EXPLICIT else None
            if rel is None:
                return str(pp)                  # outside DATA: untouched
        return str((P.DATA_ROOT / rel).resolve())
    parts = pp.parts
    if parts and parts[0] == "DATA":            # e.g. "DATA/DATABASE/lab_state.db"
        rel = Path(*parts[1:]) if len(parts) > 1 else Path()
        return str((P.DATA_ROOT / rel).resolve())
    return str(ROOT_DIR / pp)                   # untouched legacy behaviour


def load_config(path: Path | None = None) -> LabConfig:
    global _config, CONFIG_PATH
    with _lock:
        if path:
            # remember where we loaded from so runtime saves don't clobber the
            # project config (important for tests with temp configs)
            CONFIG_PATH = Path(path)
        p = Path(path or CONFIG_PATH)
        data: Dict[str, Any] = {}
        if p.exists():
            data = yaml.safe_load(p.read_text()) or {}
        # V2.7: check if CONFIG/settings.json exists to supplement or override
        if not path and SETTINGS_JSON_PATH.exists():
            try:
                js_data = json.loads(SETTINGS_JSON_PATH.read_text())
                if isinstance(js_data, dict):
                    # shallow merge top-level sections
                    for sec_k, sec_v in js_data.items():
                        if isinstance(sec_v, dict) and isinstance(data.get(sec_k), dict):
                            data[sec_k].update(sec_v)
                        elif sec_k not in data:
                            data[sec_k] = sec_v
            except Exception:
                pass
        cfg = LabConfig(
            mt5=_from_dict(MT5Config, data.get("mt5", {})),
            data=_from_dict(DataConfig, data.get("data", {})),
            evolution=_from_dict(EvolutionConfig, data.get("evolution", {})),
            backtest=_from_dict(BacktestConfig, data.get("backtest", {})),
            fitness=_from_dict(FitnessConfig, data.get("fitness", {})),
            risk=_from_dict(RiskConfig, data.get("risk", {})),
            paper=_from_dict(PaperConfig, data.get("paper", {})),
            ai=_from_dict(AIConfig, data.get("ai", {})),
            resources=_from_dict(ResourcesConfig, data.get("resources", {})),
            research=_from_dict(ResearchConfig, data.get("research", {})),
            appearance=_from_dict(AppearanceConfig, data.get("appearance", {})),
            database_path=data.get("database_path", LabConfig().database_path),
            log_level=data.get("log_level", "INFO"),
        )
        cfg.data.data_root = _resolve_data_tree(cfg.data.data_root)
        cfg.data.cache_dir = _resolve_data_tree(cfg.data.cache_dir)
        cfg.database_path = _resolve_data_tree(cfg.database_path)
        # V1 database_path pointed at <root>/lab_state.db; after layout
        # migration the db lives in DATABASE/ — follow it, never recreate.
        v1_default = str(ROOT_DIR / "lab_state.db")
        if (cfg.database_path == v1_default or "pytest" in str(cfg.database_path)) and P.database_file().exists() and not path:
            cfg.database_path = str(P.database_file())
        # V4 (LMSArena setup): DATA_ROOT is authoritative. If the configured
        # database path does not exist but the one under DATA_ROOT does, follow
        # DATA_ROOT rather than letting a new empty database be created
        # elsewhere (never create a competing/partial database).
        elif not Path(cfg.database_path).exists() and P.database_file().exists():
            cfg.database_path = str(P.database_file())
        if "pytest" in str(cfg.data.cache_dir) and not path:
            cfg.data.cache_dir = str(P.DATA_CACHE_DIR)
        _config = cfg
        return cfg


def get_config() -> LabConfig:
    global _config
    with _lock:
        if _config is None:
            return load_config()
        return _config


def update_config(section: str, values: Dict[str, Any]) -> LabConfig:
    """Update one section at runtime (from Settings page) and persist."""
    cfg = get_config()
    with _lock:
        obj = getattr(cfg, section, None)
        if obj is None:
            raise KeyError(f"unknown config section: {section}")
        if isinstance(obj, SECTIONS):
            for k, v in values.items():
                if hasattr(obj, k):
                    setattr(obj, k, v)
        save_config(cfg)
        return cfg


SETTINGS_JSON_PATH = P.CONFIG_DIR / "settings.json"


def save_config(cfg: LabConfig | None = None) -> None:
    cfg = cfg or get_config()
    with _lock:
        d = asdict(cfg)
        # portability: store root-relative paths, never machine absolutes or temp paths
        db_p = _relativize(cfg.database_path)
        if "pytest" in db_p or db_p.startswith("/tmp") or db_p.startswith("\\tmp"):
            db_p = "DATABASE/lab_state.db"
        cache_p = _relativize(cfg.data.cache_dir)
        if "pytest" in cache_p or cache_p.startswith("/tmp") or cache_p.startswith("\\tmp"):
            cache_p = "data_cache"
        d["database_path"] = db_p
        d["data"]["cache_dir"] = cache_p
        d["data"]["data_root"] = _relativize(cfg.data.data_root)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(yaml.safe_dump(d, sort_keys=False))
        # V2.7: persist to CONFIG/settings.json as well
        try:
            SETTINGS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_JSON_PATH.write_text(json.dumps(d, indent=2, sort_keys=True, default=str))
        except Exception as e:
            pass


def config_digest(sections: List[str] | None = None) -> str:
    """Stable digest of the config sections that affect experiment outcomes.

    Part of every experiment fingerprint (spec §9): change commission, fitness
    weights or death rules and the digest changes -> affected results become
    STALE instead of being silently reused.
    """
    cfg = get_config()
    sections = sections or ["backtest", "fitness", "risk", "paper", "evolution"]
    blob = {}
    for s in sections:
        obj = getattr(cfg, s, None)
        if obj is not None:
            d = asdict(obj)
            d.pop("password", None)
            blob[s] = d
    canon = json.dumps(blob, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode()).hexdigest()[:16]
