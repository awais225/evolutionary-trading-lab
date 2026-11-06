# Architecture

## Layers

```
┌──────────────────────────────────────────────────────────────┐
│ apps/web  React 18 · TypeScript · Vite · react-router-dom     │
│  shell · sidebar · instrument context · LIVE FEED (WS)         │
└───────────────┬──────────────────────────────────────────────┘
                │  HTTP /api/v1/*, /health, WS /ws/v1/events
┌───────────────▼──────────────────────────────────────────────┐
│ services/api  FastAPI · Pydantic v2 · structured JSON logs     │
│  routers: health, instruments, settings, events, nodes         │
│  central exception mapping · DI for sessions/repositories      │
└───────────────┬──────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────┐
│ packages/storage  SQLAlchemy 2.x · Alembic · SQLite            │
│  hero1_registry.db (global) + <INSTRUMENT>/DATABASE/hero1.db   │
│  explicit instrument scoping · FK enforced · composite indexes  │
├───────────────────────────────────────────────────────────────┤
│ packages/eventbus  durable events · in-memory fanout            │
│  publish() persists → call_soon_threadsafe to WS subscribers    │
│  WS clients replay history via since_id (no DB polling)         │
├───────────────────────────────────────────────────────────────┤
│ packages/strategy_spec · exec_semantics · indicators · research │
│  Phase-1 typed skeletons/enums — compile/backtest in later phases│
│ packages/ai_orchestrator  AIProvider interface · OllamaProvider  │
│ packages/mt5_adapter      safe availability probe ONLY (no orders)│
└───────────────────────────────────────────────────────────────┘
```

## Instrument isolation model

- Global **registry** DB (`DATABASE/hero1_registry.db`): `instruments`,
  `application_settings`, `global_events`. No per-instrument tables here.
- Per-instrument DB (`<INSTRUMENT>/DATABASE/hero1.db`): `nodes`,
  `node_lineage`, `backtest_runs`, `deeptest_*`, `research_*`,
  `live_sessions/config/trades`, `approvals`, `activity_events`.
- Every instrument-scoped repository call takes `instrument=` explicitly.
  Path routes are `/api/v1/instruments/{instrument}/...`; an unknown symbol
  is rejected with 404 before any DB is opened. There is no implicit default.
- Cross-test proof: `tests/test_nodes.py::test_cross_instrument_isolation`.

## Events

- Design: **durable first, live second.** `publish_event()` inserts a row into
  `global_events` (and mirrors to `activity_events` when instrument-scoped),
  then fans out in memory to WebSocket subscribers on the app event loop via
  `loop.call_soon_threadsafe` — safe from sync endpoints running in the
  threadpool. Full queues drop the oldest pending frame rather than blocking.
- Replay: `GET /ws/v1/events?since_id=N` → server sends `{type:'replay', events:[…]}`
  with up to 200 rows id>N, then live `{type:'event'}` frames. Filterable by
  `instrument`, `category`, `severity`. Frontend reconnects with exponential
  backoff and re-sends its last `since_id`; nothing is lost and nothing polls.

## API surface (Phase 1)

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | registry DB check; 5 s cached; Ollama/MT5/GPU probes 30 s cached |
| GET | `/api/v1/instruments` | seeded set (XAUUSD/EURUSD/BTCUSD) |
| GET | `/api/v1/instruments/{instrument}` | 404 when unknown |
| GET | `/api/v1/settings` | effective settings + read-only environment |
| PUT | `/api/v1/settings` | validated known keys; writes SYSTEM event |
| GET | `/api/v1/events` | filters: `category severity instrument since_id limit` |
| POST | `/api/v1/events` | manual event ingestion (used by tests/tools) |
| GET | `/api/v1/events/categories` | taxonomy + severities |
| GET | `/api/v1/instruments/{instrument}/nodes` | optional `origin` filter |
| POST | `/api/v1/instruments/{instrument}/nodes` | validates `origin`, `spec_hash` |
| GET | `/api/v1/instruments/{instrument}/nodes/{id}` | 404 cross-instrument |
| PATCH | `/api/v1/instruments/{instrument}/nodes/{id}` | name/description/status |
| WS | `/ws/v1/events` | replay + live (see above) |

OpenAPI: `/docs` (Swagger) and `/openapi.json` are always exposed.

## Error model

- Domain errors → `{"detail": "…"}` with proper status codes.
- Unhandled exceptions → 500 `{"error": {"code": "internal_error", "message": "…"}}`
  and a structured log line; stack traces never leak to clients.

## Frontend shell

- Design tokens in `src/styles/theme.css` (mission-control slate + signal amber).
- `InstrumentProvider` (context + localStorage) drives the top-bar switcher and
  every instrument-scoped API call; `FeedProvider` controls the collapsible
  global LIVE FEED; both are covered by Vitest + Testing Library tests.
- Unimplemented tabs render `Placeholder` components — never fake data.

## Deviations from the original brief (documented per spec)

1. Backend port default is **8788** (not 8787) to avoid colliding with any
   legacy service during migration.
2. Alembic lives **inside** `packages/storage` (single installable unit) rather
   than repo root — keeps migrations versioned with the models they create.
3. Event categories include **WARNING** as a first-class category (the brief
   listed ERROR) to match the severity axis used by the UI.
