# V9.1 canonical table audit — pre-implementation

Baseline: origin/main a717884604fd260024c45b9d77e56d18c11dc3fa; V8 ancestry verified by fast-forward merge. Initial tracked work preserved in Git stash and /home/user/V9_1_INITIAL_WORKTREE.patch; colliding untracked files byte-equal to published V8 were preserved separately. No reset/clean. Initial DATA preserved separately. Original LFS database SHA256 c159b0718c43b43f97609881bd3be196dad76274029949a9fb4c4e710f106a12 verified before opening.

## Traced sources
- Overview Top Strategies: three `/api/population?limit=15` callers, default fitness over complete history. Different serializer from Deep/Live. Header sort also schedules an old-closure refetch; can race the effect. Current overview does not default to frozen qualified research tier.
- DeepBacktest and LiveNodeIndex: `/api/nodes`, `limit=0`; independent table markup; Deep has client sorting that compares numeric strings lexicographically; Live uses server sorting whose mixed-type fallback is also lexicographic. Both render entire result sets.
- `/api/nodes`: stored strategies.id is canonical. get_strategy additionally synthesizes missing research aliases by run rank; inappropriate for registry projection. identity.experiment currently stamps the newest experiment rather than each row's own run.
- `/api/nodes?filter=qualified`: node_bucket qualified (SURVIVED/VALID included), excludes legacy history by default. Not the frozen research definition: stored QUALIFIED+PAPER. Preserve classifier/lifecycle semantics, change only table selection to explicit complete-history research-qualified scope.
- Five per-row SQL reads in nodes_index: config, strategy (plus optional guessed alias count), backtest, validation, dataset; another config query if configured. Entire population enriched before filtering/pagination.
- Existing shared authority `research/node_data.py` uses `_best_generation_record` from populations. That helper ranks `(rank,id)` ascending, incorrectly taking oldest within stage, while nodes_index selects newest. Fix projection ordering, never persisted metrics.
- Existing `research_shortlist` already has canonical strategy-id PK, add/remove/get methods; Live already has Starred filter. Toggle API is not retry-idempotent, accepts nonexistent ids. Reuse table via desired-state API; no generation writes, no auto-start.
- Authority cache token uses COUNT/MAX timestamps, missing direct status/metric/alias changes. Include actual SQLite local/foreign-connection revision; cache identity/limit must be distinct.

## Measured baseline (Linux sandbox, original 10,000-row LFS DB, no restore_all)
Two sequential direct route calls, SQLite trace counts, JSON encoding bytes (not HTTP gzip or browser timings):
- nodes all, page 100: 3.110 / 3.124 s; 50,032 / 50,033 SQL statements; 391,677 bytes.
- nodes qualified, unlimited: 3.173 / 3.229 s; 50,032 statements; 348,895 bytes; 92 old bucket rows on this initialized dataset. This count is an observation, not a target or production constant.
- authority cold/warm: 1.965 / .00495 s; 26 / 2 statements; 2,509,675 / 2,509,691 bytes, predominantly full identity map.
Raw evidence: /home/user/V9_1_BEFORE.json. Boot-imported operator history is a separate dataset/scope. No Windows/Edge timings measured.

## Implementation boundaries
One reusable table shell with bounded rendered pages and injected tab-specific row/action rendering; common raw numeric sorting. One canonical `/api/nodes` registry for all retained tables; bulk persisted generation/config/validation/dataset reads. Preserve scenario controls and all live/manual/risk/schedule APIs. Compact authority mode for normal frontend summaries; full identity map retained for explicit inspection/tests. No new polling or eager jobs.

## Implemented and verified follow-up

- All three retained tables now render through `components/NodeTable.jsx` (shared paging engine, `NodeIdentity`, `CandidateStar`), injecting the retained tab-specific headers/rows/actions. Deep/Live still sort the complete matching set; at most100 node rows are mounted per render page. Overview's top15 is a labelled, selectable qualified/all/starred registry view, not `/population` inventory. Existing HTTP GET in-flight coalescing is reused; no new poller.
- `/api/nodes` is the canonical registry response for Overview/Deep/Live. Frozen status QUALIFIED+PAPER over complete stored history is the default; broader unchanged classifier selection survives under explicit `gate_passed`. Bucket inventory counts remain separately labelled. Existing live tradeability/lifecycle predicates are unchanged; starred-only exposes saved candidates even if not eligible, with the existing reason.
- Bulk persisted-record reads replace per-node SQL. Generation scalar extraction stays in SQLite; saved trade/equity arrays are fetched only on detail/Inspect paths. A revision-keyed summary cache is shared by node lists and authority. Missing values and aliases remain missing; row provenance uses its own run, not the newest experiment.
- Freshness uses additive `node_registry_revision` + triggers on strategies/backtests/datasets, including direct SQL and writes from other connections. Shortlist writes do not invalidate that revision. This supersedes the initially considered local total_changes/data_version approach, which would invalidate on unrelated star/meta writes.
- Desired-state `PUT /research/shortlist/{canonical_id}` uses existing research_shortlist, rejects nonexistent ids/nonboolean desired states, and inserts only if absent (retry preserves original notes/time). Shared star controls update the affected row with no population request; navigation/restart rereads durable state. No worker/order/generation API is called.
- The detail `get_strategy` getter also no longer fills absent aliases with a run rank. Research shortlist projection no longer substitutes canonical id for an absent research number or invents a run label. Strategy Lab's existing stored identity columns and all generation/risk logic are retained.

### Important real-data identity finding
Running the existing restoration engine against the verified original DB plus genuine saved research files produced **10,809 rows / 99 stored QUALIFIED+PAPER**. The saved top-return generation record remains canonical **Node #718**, XAUUSD M1, stored SURVIVED, return1.481, PF2.545, DD.0405, WR.7724, trades905. Its **persisted research_node_num is NULL** in this restored dataset. Earlier detail output `research #318` was the old getter's computed run-rank fallback, NOT evidence of a persisted alias. V9.1 intentionally displays that alias as missing. The collision tests persist explicit aliases in their own fixture; those fixture values are not asserted to be the operator's data. No records were manufactured/requalified to obtain99 or718.

Full final measurements, completed gates, hashes and environment limitations are in `V9_1_REPORT.md`.

The final shared table also renders the **same `GenerationCells` component** on all three pages: saved Gen Return/Net/PF/DD/WR/Trades, with record provenance tooltips. Live's previous effective-performance cells remain separate and unchanged; generation cells are appended so no lifecycle/risk/action control is displaced. Mounted cross-tab tests compare the same saved generation value in all three. Candidate events also synchronize the App shortlist context (no whole-population request), preserving other retained shortlist consumers. Deep coverage now reacts to the actual loaded nodes and duration changes, fixing an existing stale-empty-node closure; mounted tests verify initial and3Y persisted coverage requests.
