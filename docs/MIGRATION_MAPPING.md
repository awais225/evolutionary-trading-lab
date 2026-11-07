# Legacy → Hero1 Migration Mapping

Source of truth for reuse decisions, derived from an inspection of the legacy
Evolutionary Trading Lab at tag/branch **v10.4-throughput** (commit
`85b79b1c4097b026111262e300e442814664db31`, read-only worktree, nothing
modified). The legacy repo is **reference only** — its code is not copied
wholesale; proven logic is re-implemented against Hero1's modular packages.

| # | Previous dashboard functionality | Hero1 destination | Status in this milestone |
|---|---|---|---|
| 1 | MT5 terminal discovery (`backend/app/mt5/discovery.py` — dir scan of program files / `%APPDATA%\MetaQuotes\Terminal`, `validate_terminal_path`, `normalize_terminal_path` folder→exe handling) | `packages/mt5_adapter` → `connection.discover_terminals()` | Ported (patterns reused; fresh implementation, Windows-only scan, honest empty list elsewhere) |
| 2 | MT5 init/terminal info (`mt5_real.py` — `SESSION_LOCK`, `session_snapshot()`, `restore_session_from_snapshot()`; one session lock for IPC create/destroy) | `packages/mt5_adapter/connection.py` — single `SESSION_LOCK`, `status()` reports package/terminal/account identity | Pattern ported (smaller surface; no execution path copied) |
| 3 | Account info (`accounts.py` — `probe_terminal()` read-only probe per terminal, trade_mode names 0/1/2 = DEMO/CONTEST/REAL, password stripping) | `mt5_adapter.connection.account_snapshot()` + API `GET /api/v1/mt5/status`; account → Live Testing selector only | Ported (account fields normalized from real `account_info()._asdict()`; missing fields stay `None`, never fabricated) |
| 4 | Multiple account/terminal handling (`accounts.py`, `mt5_config.json` — saved path + discovery; account switch re-initializes the right terminal; never silently submits credentials) | TopBar MT5 dropdown → `POST /api/v1/mt5/connect {terminal_path}`; active terminal recorded in `application_settings` (`mt5.active_terminal_path`) — **Live Testing context only** | Implemented; account isolation rule: selection never touches instrument/nodes/fingerprints/DATA |
| 5 | XAUUSD point/pip/tick sizing (`backtest/symbol_specs.py` — `pip_size_from_digits` rule "pip = 10·point on 3/5-digit feeds else point", `round_to_tick`, `stops_distance_check`; explicitly never from symbol name) | `packages/mt5_adapter/sizing.py` — same digits rule (attributed), `round_price_to_tick`, `normalize_volume`, `points_per_pip` | Ported verbatim logic + unit tests |
| 6 | Instrument metadata registry (legacy `CONFIG/symbol_specs` + `symbol_info()`) | `packages/mt5_adapter/metadata.py` — normalized snapshots stored per **broker/server+symbol** under `DATA/MARKET_METADATA/<server>/<SYMBOL>.json` with manifest + SHA-256; validation consumes `{SYMBOL: {point/tick/pip/contract}}` | New, modeled on legacy precedence (MT5 > anything; never a silent table) |
| 7 | Historical candle retrieval (`verify_mt5_historical_windows.py` — `copy_rates_range`, coverage reporting, read-only) | Future `fetch_history()` in adapter (not used by this milestone; CSVs are the source) | Deferred by design (this milestone imports owner-supplied CSVs) |
| 8 | CSV ingestion + validation (legacy `V9.2` — all 419,013 rows validated: finite OHLC, ordered, 0 duplicates; tab-separated MT5 header `<DATE>,<TIME>,...`; server-time convention user-confirmed IC Markets GMT+2/+3 seasonal; originals byte-identical, never fabricated bars) | `scripts/import_market_data.py` — same header map, same zero-fabrication rules, OHLC/order/duplicate validation, gap *reporting* without inventing bars; normalized SQLite store + JSON manifest (source path/SHA-256/byte-count retained; original CSVs stay untouched where the owner keeps them - nothing is copied or rewritten) | Ported as Hero1 import pipeline + tests |
| 9 | Database storage / partitions (legacy monthly partitions + meta) | Hero1: dedicated read-only-after-import SQLite store `DATA/MARKET_DATA/market_data.db` (`candles` + `datasets`), manifest.json with checksums — fits existing SQLite architecture, no duplicate DBs | New (legacy partitions not copied) |
| 10 | Backend ↔ frontend communication (FastAPI + React, `/health` echoing mt5 status) | Hero1 API (`services/api`) + React TopBar MT5 dropdown | Extended (health already echoes mt5; new endpoints added) |
| 11 | Live Testing account selection (`LiveTestingControl.jsx`, `live_testing/` — read-only executenda gated behind explicit user activation; demo-account guard) | Account selection exposed in top bar + `application_settings`; execution remains a separate, gated future phase (**no orders in this milestone**) | Selection implemented; execution deliberately NOT copied |
| 12 | DeepTesting pipeline (`V10.4-throughput` backtest engine, canonical store, qualification) | Hero1 DeepTesting milestone (future) consumes `MARKET_DATA` store via manifest | Deferred; provenance hooks provided |
| 13 | Strategy specification/compilation (legacy node JSON + fingerprints) | Hero1 typed spec (`packages/strategy_spec`) + deterministic compiler — unchanged semantics, extended fingerprint inputs only via direction (already present) | Preserved; NEEDS_METADATA fixed by wiring real metadata (Part 4) |
| 14 | MT5 execution order semantics (`order_semantics.py` — filling bitmasks, FOK/IOC/RETURN, stops level) | Future execution package; NOT part of this milestone | Not copied (out of scope, execution is gated) |
| 15 | Error handling & diagnostics (legacy `windows_diagnostic.py`, logs to LOGS/, rc discipline) | Hero1 `:STAGE_FAILED` launcher, `START_DIAGNOSTIC.bat`, `TROUBLESHOOTING.txt` knowledge base | Extended |

**Migration rule honored:** nothing from legacy is imported wholesale; Hero1 keeps
its typed spec/validation/compiler architecture. Legacy knowledge is reused as
(a) proven conversion rules, (b) warning signs documented in TROUBLESHOOTING.txt,
(c) test-gap awareness. No legacy DATA is copied into Git; the legacy worktree
was read-only and is removed after this audit.
