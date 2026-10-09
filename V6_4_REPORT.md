# V6.4 RELEASE REPORT — execution, identity, metrics and live-testing corrections

Release label ....... V6.4 (`backend/app/versions.py` PRODUCT_RELEASE = "V6.4")
Date ................ 2026-10-09
Starting baseline ... `e4700493f9075614b112c5c7acdc809cec1f26d6`
                      ("V6.3 MERGE V6.1 REMOTE HISTORY WITH V6.2 DATA" — verified
                      equal to `origin/main` at pre-flight)
Latest DATA upload .. `85bc9450` ("V6.2 DATA FOLDER UPDATED" — the newest commit
                      touching `DATA/`; history preserved untouched by V6.4)
Release commit ...... see "Git publication" below (fingerprint entry carries the
                      exact SHA)

V6.4 is a corrective release. Every defect was traced to a root cause in code
before any code changed; each fix lands at the narrowest point; no expected
value is hard-coded; nothing was relabelled instead of fixed.

Evidence classes used below:
  * FIXTURE evidence  — deterministic tests with fake brokers/DBs (no broker).
  * API evidence      — the real FastAPI app exercised through TestClient.
  * DB evidence       — queries against the real study database snapshot.
  * BROKER evidence   — a real MT5 terminal; NOT available on this Linux host.
    All broker-side claims are marked PENDING WINDOWS MT5 VERIFICATION.

---------------------------------------------------------------------------
0. DATA SAFETY (verified before and after every test run)
---------------------------------------------------------------------------
Authoritative DATA root : /tmp/evolutionary-trading-lab-DATA/DATA
                          (outside the workspace; never copied INTO the repo)
Database                : DATA/DATABASE/lab_state.db (139,304,960 bytes)
sha256 PRE  (09:16 UTC) : eeb456a1db0af215c64700fa7a8657b34e4f5d92ef119b5f46bdab4a43813652
sha256 POST (after runs) : eeb456a1db0af215c64700fa7a8657b34e4f5d92ef119b5f46bdab4a43813652
                          — IDENTICAL; this session mutated NOTHING.
Integrity / shape       : PRAGMA integrity_check = ok; 10,000 USER_RESEARCH rows,
                          ids 788..10787, no LEGACY_TEST rows in this snapshot.
Recovery                : DATA/BACKUPS/*.zip (5 historical backups, 2026-09-27..
                          2026-10-04) + the git-tracked LFS object at e4700493
                          (the pristine V6.2 DATA upload) are the untouched
                          recovery sources.
Test isolation          : EVERY test run used byte-identical scratch copies
                          (/var/tmp/evolutionary-lab-SCRATCH*/DATA, same sha256)
                          with EVOLUTIONARY_LAB_DATA_ROOT pointing AT the DATA
                          directory. Scratch copies self-mutate heavily by design
                          (research-run workflow tests insert/re-tag rows) — which
                          is precisely why the authoritative root must not be used.

DISCOVERY (prior-session mutation, documented honestly): at THIS session's start
the authoritative DB already carried run_id 'RUN-20261009-051720' on all 10,000
rows (a timestamp from an earlier test run in this workspace) and a
`research_shortlist` table. Rows, ids, statuses, metrics and the schema are
intact (10,000 rows, ids 788..10787, integrity ok). The V6.4 commit contains
ZERO DATA changes (sparse checkout "/*, !/DATA"; staged set is source/tests/docs
only).

---------------------------------------------------------------------------
1. DEFECT #1 — XAUUSD pip conversion (CRITICAL)
---------------------------------------------------------------------------
Symptom: manual panel said "100 pips" while the submitted prices implied a
$10.00 distance (BUY ~4190.79, SL 100 pips -> 4180.79): a pip of 0.10.

Root cause: pip size was resolved at scattered call sites behind a name/size
heuristic; the calculator, preview and order request could each use a different
conversion, and nothing refused when the symbol spec was unknown.

Convention (CONFIRMED against MT5_BRIDGE_DETAILS.txt "PIP SIZE (the digits
rule)", lines 80-84 / 324-328 — the handoff document is the MT5 authority):
  digits=2 -> point=0.01 -> pip = 1 x point = 0.01
  digits=3 -> point=0.001 -> pip = 10 x point = 0.01
  digits=4 -> point=0.0001 -> pip = 1 x point = 0.0001
  digits=5 -> point=0.00001 -> pip = 10 x point = 0.0001
XAUUSD on a 2-digit feed: digits=2, point=0.01, pip=0.01 — so 100 pips = $1.00
and 600 pips = $6.00. The document warns a wrong pip "puts your stop loss ten
times too far" — exactly the observed defect. One convention, derived from
symbol_info().digits/point. If digits/point cannot be established, the order is
REFUSED with a diagnostic (never a guessed pip).

Fix (narrowest point: one conversion module):
  * backend/app/backtest/symbol_specs.py — canonical helpers:
    pip_size_from_digits, points_per_pip_from_digits, pips_to_price,
    price_to_pips, round_to_tick, stops_distance_check, pip_conversion_report;
    SymbolSpecs.pip_size raises when the convention is unestablished; name-
    heuristic fallbacks removed.
  * backend/app/api/routes.py (manual order) — one conversion for preview AND
    request prices; the preview reports resolved digits/point/pip/points-per-
    pip and the exact SL/TP price distances; SL/TP rounded to tick size;
    stops-level/freeze-level validated before send (refusal retcode -8);
    unestablished convention refuses with retcode -7; R:R and risk sizing use
    the same pips_to_price; BUY/SELL directions unchanged and tested.
  * backend/app/mt5/windows_diagnostic.py — same rule in the diagnostic print.
  * frontend LiveTestingPanels.jsx — pip-conversion strip on the panel and the
    exact price distances in the confirm modal (UI label == submitted distance).

Evidence: FIXTURE — tests/test_v6_4_pip_conversion.py, 24 tests: 2/3/4/5-digit
feeds (incl. the actual XAUUSD 2-digit config), preview==request agreement,
tick rounding, stops-level refusal, refusal when unestablished. No live order
was sent anywhere in this release.

PENDING WINDOWS MT5 VERIFICATION: read XAUUSD symbol_info().digits/point from
the live terminal and confirm the preview shows pip=0.01 and $1.00 for 100 pips.

---------------------------------------------------------------------------
2. DEFECT #2 — MT5 result normalization (false UNKNOWN)
---------------------------------------------------------------------------
Symptom: broker answered retcode 10009 with deal/order tickets and fill price;
the UI classified the outcome UNKNOWN.

Root cause: app/mt5/mt5_real.py serialized the order_send result into a dict
without reading attribute-style result fields (namedtuple/__slots__/properties/
foreign binding objects), so an executed trade lost its retcode/deal/order and
was surfaced as unreadable.

Fix (mt5_real.py only):
  * _RESULT_FIELDS + new _result_to_dict: reads retcode, deal, order, volume,
    price, bid, ask, comment, request_id, retcode_external and extension fields
    directly (getattr), whatever the object shape; normalizes numpy-style ints;
    keeps raw diagnostics (_repr/_type/_dict_keys) for odd types.
  * place_order/_send_with_filling_fallback: retcode 10009 is success ONLY when
    the result confirms execution; any other readable retcode travels verbatim;
    None / exception / unreadable results stay distinct from both success and
    rejection; uncertain outcomes are NEVER auto-resend (one send site kept).
  * Demo-only gates, filling-mode fallback (10029/10030 only), session handling
    and the proven order construction are UNCHANGED (verified against the V6.3
    baseline: the only diff is the normalization + the ok-on-10009 clause).

Evidence: FIXTURE — tests/test_v6_4_result_normalization.py, 11 tests: a real-
shaped success object (attribute fields), dict result, None, exception, bad
retcode, unrecognized object shape, string/numpy retcodes, request_id/comment
preservation. These are fixtures, NOT broker executions.

PENDING WINDOWS MT5 VERIFICATION: send one demo order and confirm the panel
shows retcode/deal/order/volume/price/comment (no UNKNOWN on a DONE result).

---------------------------------------------------------------------------
3. DEFECT #3 — canonical node identity
---------------------------------------------------------------------------
Symptom: Overview showed "node #6658 (6.71%)" while the detail page said
"Research Node #5,871" and the node seemed absent from the Live Testing
qualified table.

Root causes (two, both traced):
  (a) mixed identity namespaces: DB primary key vs study-local display number
      (research_node_num) vs generation vs label. The study contains DB id 6658
      (research number 5,871, status QUALIFIED) AND a different node whose
      research number is 6658 (DB id 7445, status RETIRED). Surfaces used bare
      numbers, so the same number named different nodes.
  (b) found while auditing (reproduced by the full suite, fixed here):
      nodes_index scoped its ROWS to "the run_id of the newest strategy row"
      while counts/filter_totals/populations described the whole table — so one
      fixture row inserted under another run id emptied the table ("authority
      says 5,259 qualified, the table says 0") — the exact family of
      "stale mapping/empty table" symptoms the audit asked for.

Fix:
  * Canonical identity = database row id, displayed "Node #<id>" everywhere;
    the study-local number is the explicitly-labelled secondary "research
    #<num>" (frontend ui.jsx nodeIdentity/nodeLabel/researchLabel/
    nodeLabelFull; StrategyDrawer title "Node #id · research #num";
    Overview/Deep Testing/Final Testing/EvolutionTree/BacktestMatrixTable).
  * backend/app/api/routes.py nodes_index: every row carries node_label,
    research_label and an identity block spelling the contract; search matches
    BOTH namespaces with labels ("6658" finds Node #6658 AND research #6658,
    never one bare number for two nodes); rows, total, counts and
    filter_totals are ONE universe (the whole index: every non-legacy row plus
    the legacy rows that form the "excluded" bucket) so total == counts by
    construction and matches app.research.populations; an explicit run_id
    parameter remains a deliberate filter; each row keeps its own run_id and
    the experiment block names the current experiment plus other_runs.
  * QUALIFIED appears iff the canonical criteria are met (status classifier)
    and the node is in the study — nothing forced in.

Node 6658 outcome (DB evidence on the real study snapshot):
  * DB id 6658 = "Node #6658 · research #5,871" — status QUALIFIED, metrics
    6.71% return — present in the QUALIFIED filter (it meets the criteria).
  * DB id 7445 = "Node #7445 · research #6658" — status RETIRED — correctly
    ABSENT from QUALIFIED. The overlap is now unambiguous on every surface.

Evidence: FIXTURE/API — tests/test_v6_4_node_identity.py (6 tests, incl. a
node whose DB id ≠ study-local number), cross-endpoint identity checks in
tests/test_v5_1a_qualified_nodes.py::test_05 and the live-index suites.

---------------------------------------------------------------------------
4. DEFECT #4 — performance metrics verification
---------------------------------------------------------------------------
Symptom: node 6658 showed net +670.82, return 6.71%, PF 2.91, maxDD 0.6%,
win 70.5%, 44 trades — and readers interpreted "PF 2.91" as "291% return".

Root cause: the numbers themselves are internally consistent (verified
independently below); the defect was presentation/unit linkage: the detail
drawer divided an already-fractional return by 100 AGAIN (showing "0.07%"
instead of "6.71%"), the PF carried no unit ("ratio"), and no surface stated
the denominator/definition or which evaluation the page described.

Independent ledger reconciliation (DB evidence — backtest id 9478, the
'detail' evaluation of node 6658):
  gross profit          1,022.16
  gross loss              351.34
  net profit             +670.82 = 1022.16 - 351.34          [CURRENCY]
  initial capital      10,000.00 (derived: final_equity 10,670.82 - net)
  return               0.0671 = 670.82 / 10,000             [FRACTION = 6.71%]
  profit factor        2.909 = 1022.16 / 351.34             [RATIO, never 291%]
  max drawdown         0.0058                                [FRACTION = 0.58%]
  win rate             0.7045 = 31 / 44                      [FRACTION = 70.5%]
  trades               44                                    [COUNT]
  Every metric refers to ONE evaluation (backtest id 9478). PF 2.91 is a
  ratio; 6.71% is net/initial-capital. They reconcile exactly.

Fix:
  * frontend NodeDetailDrawer.jsx — the /100 double-division bug removed;
    "Profit Factor (ratio)" labels + tooltips; "Return %" labelled as a
    percentage of initial capital.
  * backend/app/strategies/authoritative.py — the economics payload gains an
    evaluation block: backtest_id, stage, fingerprint, window and the derived
    initial capital, plus the unit/definition contract for every metric
    ("FRACTION"/"RATIO — never 291%"/"COUNT"/"ACCOUNT CURRENCY"), the return
    denominator and the drawdown convention. Monthly PnL and exit reasons
    belong to the same evaluation. Data linkage fixed at the source; no
    historical value altered.

Evidence: tests/test_v6_4_metrics_contract.py (7 tests) — deterministic
fixtures with independently recomputed ledgers (incl. the PF zero-gross-loss
policy: PF is reported as a ratio and flagged when gross loss = 0) AND a live
reconciliation of the real node-6658 ledger (PASS, table above).

---------------------------------------------------------------------------
5. DEFECT #5 — Live Testing START/STOP lifecycle (+ STOP ALL)
---------------------------------------------------------------------------
Root cause: worker state was implicit (no vocabulary, no task identity),
stop raced in-flight cycles, a worker that finished during startup was dropped
from the registry (lost state), there was no STOP ALL, and a page refresh
could not reconstruct the true backend state.

Fix (backend/app/live_testing/workers.py rewrite + routes):
  * Explicit states: IDLE / STARTING / RUNNING / STOPPING / STOPPED /
    COMPLETED / ERROR. IDLE = never started (or fresh probe after restart).
  * Every start carries a fresh task_id; state replies name the task_id that
    produced them (a stale reply is detectable); a duplicate START is refused
    with the RUNNING worker's identity — never a second worker/thread.
  * STOP = STOPPING -> the in-flight cycle finishes (_cycle_lock boundary) ->
    no new cycle -> read-only MT5 reconcile (reports this node's open
    positions; NEVER closes any) -> STOPPED. stopping ≠ closing positions.
    The final-state entry is kept, so a refresh reports STOPPED (never an
    anonymous probe). COMPLETED is kept likewise.
  * NEW: POST /api/live-testing/nodes/stop-all (per-node results,
    positions_touched: false); GET /api/live-testing/workers (all states +
    worker_states vocabulary); worker state endpoint per node.
  * A STOPPING/STOPPED worker places NO new trades (stop flag checked before
    every evaluation). In-flight order boundary: the single evaluation that is
    already inside the engine finishes; no new one starts; the reconcile
    compares with MT5 before the final state is reported.

Evidence: tests/test_v6_4_live_lifecycle.py (14 tests: two nodes running, stop
one and not the other, STOP ALL, refresh persistence, repeated START/STOP
clicks, startup races incl. a worker completing during startup, backend
errors) + legacy suite tests/test_v6_live_testing_workers.py updated to the
explicit vocabulary (obsolete "idle forever for un-enrolled nodes" fixture
contract superseded: a worker whose node is un-enrolled COMPLETES).

PENDING WINDOWS MT5 VERIFICATION: run two nodes on the demo terminal, STOP
one and observe it places no further trades while the other continues; STOP
ALL; confirm open positions remain until closed explicitly.

---------------------------------------------------------------------------
6. DEFECT #6 — schedule defaults from provenance (+ timezone)
---------------------------------------------------------------------------
Root cause: defaults could be inferred from unrelated genome fields (a bare
symbol/timeframe treated as provenance), and the schedule description omitted
the timezone, so displayed, submitted and enforced schedules could diverge.

Fix:
  * backend/app/api/routes.py _node_schedule_provenance: defaults come ONLY
    from the node's stored live-testing config, then its genome's own
    days/sessions/windows. A bare symbol/timeframe is NOT provenance.
  * START without provenance returns 409 SCHEDULE_REQUIRED with a precise gap
    note and the deliberate options (select explicitly, or send
    schedule={"confirm_product_default": true} for the documented product
    default: Mon-Fri, all sessions, node's own timeframe, UTC). Nothing is
    invented.
  * The submitted schedule is normalized, persisted verbatim and returned as
    schedule_resolved/schedule_source/schedule_timezone; the backend enforces
    it every cycle (schedule.evaluate gate in the worker/engine path).
  * backend/app/live_testing/schedule.py describe(): every description appends
    " (UTC)" — the applicable timezone is always visible.

Evidence: tests/test_v6_4_schedule_defaults.py (9 tests: displayed == submitted
== enforced; provenance precedence config > genome days/sessions/windows;
missing-provenance refusal; timezone; persistence).

---------------------------------------------------------------------------
7. DEFECT #7 — live position table + scoped close controls
---------------------------------------------------------------------------
Root cause: no position surface existed and the old close-positions endpoint
was a 501 stub; nothing could show or safely close what the broker has open.

Fix:
  * GET /api/mt5/positions — the terminal's real open positions: ticket,
    symbol, side, volume, open/current price, SL, TP, floating PnL, magic,
    comment, open time (ISO). Frontend LivePositionsPanel.jsx renders the
    table on the MT5 demo page with account-safety state.
  * POST /api/mt5/positions/close — CLOSE one position BY ITS BROKER TICKET.
    Without confirmed=true: 409 CONFIRMATION_REQUIRED listing exactly
    would_close. The close is the broker's own opposite-deal form (position
    ticket, position symbol/volume, current tick, the symbol's filling mode).
    _close_one_verified reports CLOSED_VERIFIED only when the broker answered
    DONE (retcode 10009) AND the position is gone from positions_get — never
    on submit alone (CLOSE_UNCONFIRMED / CLOSE_FAILED otherwise). The response
    returns the refreshed position list.
  * POST /api/mt5/positions/close-all — CLOSE ALL requires an EXPLICIT scope
    ({symbol}, {magic}, {account_login} or {all_on_account}); an account
    mismatch closes nothing (ACCOUNT_SCOPE_MISMATCH); positions with other
    magic numbers are NEVER touched silently — they need
    acknowledge_other_magic=true and the refusal names them; the confirmation
    lists exactly what will close; each close is individually verified and the
    refreshed list reports the result.
  * Stale/already-closed tickets send NOTHING (the broker form is never
    built); a partial close is never reported fully closed.
  * Demo-only: all close controls refuse unless the connected account is a
    positively identified DEMO account (403 otherwise).
  * Stopping a live-testing task NEVER closes positions (defect #5 reconcile
    is read-only; asserted in both suites).

Evidence: FIXTURE — tests/test_v6_4_positions_close.py, 14 tests: table fields,
exact-ticket targeting, confirmation requirements, verified close, unverified
never claimed, broker rejection, demo-only, explicit scope, exact would_close
list, scope-limited close-all + post-close reconciliation, other-magic
authorization, account-scope mismatch, stale ticket, partial close, "listing
never closes".

PENDING WINDOWS MT5 VERIFICATION: open two demo positions (one lab magic, one
manual/other magic); CLOSE one by ticket; CLOSE ALL on a symbol scope; verify
the other-magic acknowledgement flow and that closed_verified appears only
when the terminal shows the position gone.

---------------------------------------------------------------------------
8. TEST RESULTS (exact, this tree, this session)
---------------------------------------------------------------------------
Command (focused): python -m pytest tests/test_v6_4_*.py -q
  V6.4 suites: 85 passed
    test_v6_4_pip_conversion.py ......... 24
    test_v6_4_result_normalization.py ... 11
    test_v6_4_node_identity.py ..........  6
    test_v6_4_metrics_contract.py .......  7
    test_v6_4_live_lifecycle.py ......... 14
    test_v6_4_schedule_defaults.py ......  9
    test_v6_4_positions_close.py ........ 14

Command (release gate): EVOLUTIONARY_LAB_DATA_ROOT=<pristine scratch>
  python -m pytest tests/ -q
  RESULT: 939 passed, 1 skipped, 0 failed in 281.28s (1 warning:
  starlette TestClient httpx deprecation).
  The single skip is environmental and pre-existing:
  tests/test_v5_1a_coverage_and_balances.py:120 "this installation carries no
  simulator dataset".

V6.3 baseline comparison (identical protocol, pristine scratch copy of the
same DATA, worktree at e4700493): 12 failed, 842 passed, 1 skipped in 170.96s.
All 12 baseline failures are resolved in the V6.4 gate run; none were dismissed
as "pre-existing" without reproduction, assertion inspection and attribution:
  * 8 (live-index 01/02/03/05, populations 24, qualified 06/09, forensics 20)
    — the nodes_index universe defect (section 3b): rows scoped to the newest
    row's run while counts described the whole table. FIXED.
  * launcher test_11 — frontend_build_guard --scan exited 1 on a completed
    report ("nothing found"); --scan is a read-only report (documented exit
    code added; SCAN_FOUND emitted). FIXED.
  * v4_1 test_state / v4_2 test_legacy — DATA-shape assumptions ("the DB
    always carries LEGACY_TEST rows at ids 1..791"). Contracts strengthened to
    verify the exclusion EXACTLY against the database's real classification
    (equality, not a population mix) — they pass on this snapshot (0 legacy
    rows) and on the operator's mixed snapshot (787 legacy rows).
  * live-testing test_17 — order-effect of the old shared-state lifecycle;
    green under the rewritten worker registry.

Also corrected during the audit (contract supersession, tests updated — never
weakened): tests/test_v5_live_testing.py test_09 (description must state the
timezone — "(UTC)"), test_16 (START without provenance must be refused, then
start with a deliberate schedule), tests/test_v6_live_testing_workers.py (the
explicit state vocabulary IDLE/STOPPED/COMPLETED; thread naming
LiveTestWorker-<node>-<task>; stub presents an ENROLLED node as the API
guarantees). Population-mix assertions ("failed > qualified") were replaced
with the real invariants (the default lists only qualified rows; every filter
reports its own honest count) — the proportion is study mix, not contract, and
the suite's own research-run tests rewrite the shared scratch DB's statuses
(measured: RETIRED/KILLED -> SURVIVED/NOT_TESTED across ~5,000 rows).

Frontend: no unit-test script exists (package.json scripts carry build/dev
only). Frontend verification = production build (below) + the frontend
contract/smoke suites inside the gate run (test_v4_7_frontend,
test_v4_8_frontend, test_frontend_serving, test_v5_1a_next_ui_contract — all
green).

API contract/shape tests: test_v5_1a_next_ui_contract.py, test_v5_2_3_*,
test_v5_1a_qualified_nodes.py, test_v6_mt5_bridge_contract.py and the V6.4
suites above — all green in the gate run. Regression proof of no DATA/DB
study-reset side effects: the authoritative DB sha256 is identical pre/post
(section 0); no test in the release run used the authoritative root.

---------------------------------------------------------------------------
9. PRODUCTION BUILD + FINGERPRINT
---------------------------------------------------------------------------
Build: cd frontend && npm run build — OK (vite, 62 src files;
  entry assets: assets/index-B9dWSF5o.js, assets/index-DG45pVtK.css;
  index.html sha256 f2ac507b7c2639c2...).
Guard: backend/tools/frontend_build_guard.py --stamp then --check —
  GUARD_STATUS=FRESH, GUARD_OK=True ("frontend/dist was built from the
  current frontend/src").
Fingerprint: backend/tools/build_fingerprints.py --record --release V6.4
  release V6.4, src_hash 300f7ef19b868f86..., code_hash 100e3cb7497f7c62...,
  index_sha256 f2ac507b7c2639c2...; --match identifies this tree as V6.4
  (FINGERPRINT_MATCH: V6.4). PRODUCT_RELEASE ("V6.4") equals the newest
  published release (test_16 pins this permanently). After the release commit
  exists the fingerprint's commit field is refreshed to that SHA with the same
  tooling (see Git publication) so the published identity names a commit that
  exists and the recorded hashes still describe the delivered build.

---------------------------------------------------------------------------
10. FILES CHANGED (source / tests / docs — no DATA, no secrets, no caches)
---------------------------------------------------------------------------
Backend (product fixes):
  backend/app/backtest/symbol_specs.py .... pip/digits convention + helpers
  backend/app/api/routes.py ............... manual order pip/stops/refusal;
                                            nodes_index identity + universe;
                                            schedule provenance + SCHEDULE_
                                            REQUIRED; START/STOP/stop-all/
                                            workers/schedule-provenance;
                                            positions/close/close-all
  backend/app/mt5/mt5_real.py ............. _RESULT_FIELDS + _result_to_dict;
                                            ok only on confirmed retcode 10009
  backend/app/mt5/windows_diagnostic.py ... digits-rule pip print
  backend/app/live_testing/workers.py ..... lifecycle rewrite (7 states,
                                            task_id, stop boundary, reconcile,
                                            stop_all, kept final states)
  backend/app/live_testing/schedule.py .... describe() states the timezone
  backend/app/strategies/authoritative.py . evaluation block + unit definitions
  backend/app/versions.py ................. PRODUCT_RELEASE = "V6.4"
  backend/tools/frontend_build_guard.py ... --scan is a report (exit 0 +
                                            SCAN_FOUND); documented exit code
Frontend:
  frontend/src/components/ui.jsx ......... nodeIdentity contract
  frontend/src/components/LivePositionsPanel.jsx (NEW) positions + close UI
  frontend/src/components/LiveNodeIndex.jsx ..... labels, STOP ALL, worker
                                            state column, schedule on START
  frontend/src/components/LiveTestingPanels.jsx . pip strip + confirm distances
  frontend/src/components/StrategyDrawer.jsx .... identity title + tooltips
  frontend/src/components/NodeDetailDrawer.jsx .. /100 bug, PF (ratio)
  frontend/src/components/BacktestMatrixTable.jsx, pages/Overview.jsx,
  pages/EvolutionTree.jsx, pages/FinalTesting.jsx, pages/Mt5DemoTrading.jsx,
  frontend/src/api.js ..................... new API methods
Tests (new): tests/test_v6_4_{pip_conversion,result_normalization,node_
  identity,metrics_contract,live_lifecycle,schedule_defaults,positions_close}.py
Tests (updated to corrected contracts): test_v5_manual_order.py,
  test_v5_1a_manual_calculator_flow.py (kept in the intended repository test
  directory; tracked), test_v5_1a_live_index_and_risk.py,
  test_v5_1a_mt5_order_diagnostics.py, test_v5_1a_qualified_nodes.py,
  test_v5_3_demo_execution_forensics.py, test_v4_5_strategy_lab.py,
  test_v5_live_testing.py, test_v6_live_testing_workers.py,
  test_v4_1_research_run_workflow.py, test_v4_2_mt5_execution.py
Docs/meta: V6_4_REPORT.md (this file), lmsarena.txt (entry), chatgpt.txt
  (request record), BUILD_FINGERPRINTS.json (V6.4 entry)

---------------------------------------------------------------------------
11. WINDOWS / MT5 DEMO MANUAL VERIFICATION CHECKLIST (the only way to close
    the broker-side items on this Linux host)
---------------------------------------------------------------------------
[ ] 1. Run CHECK_MT5_WINDOWS.bat: terminal connected, account 53071066
       (ICMarketsSC-Demo), Build chip shows V6.4 + fingerprint match.
[ ] 2. XAUUSD symbol spec: digits=2, point=0.01 -> panel preview shows
       pip=0.01, "100 pips" = $1.00, "600 pips" = $6.00 (NOT $10/$60).
       If the live spec differs (e.g. digits=3), the preview must show the
       resolved rule and the same conversion everywhere.
[ ] 3. Manual order (DEMO, 0.01 lots): preview distances == submitted
       distances; SL 100 pips lands $1.00 from entry; SL/TP on tick.
[ ] 4. Result panel on the demo order: retcode 10009 + deal/order tickets +
       fill volume/price/comment/request_id visible; no UNKNOWN label.
[ ] 5. Identity: "Node #6658" opens the same node from Overview, Top
       Strategies, detail, Deep Testing and Live Testing; label shows
       "research #5,871"; searching 6658 finds both namespaced nodes.
[ ] 6. Live Testing: START one node with a schedule (and confirm a node with
       no provenance is refused with SCHEDULE_REQUIRED); row shows RUNNING +
       STOP; STOP confirms STOPPED; repeated clicks are safe; refresh shows
       the backend state; STOP ALL stops every worker; a stopped node places
       no new trades; open positions remain (stopping never closes).
[ ] 7. Positions panel: real tickets/symbols/sides/prices/SL/TP/PnL; CLOSE
       one by ticket -> CLOSED_VERIFIED only when it disappears from the
       terminal; CLOSE ALL with a symbol scope + confirmation list; the
       other-magic flow requires explicit authorization.
[ ] 8. Re-run CHECK_MT5_WINDOWS.bat after the session: identity still V6.4.

---------------------------------------------------------------------------
12. GIT PUBLICATION
---------------------------------------------------------------------------
(Publication record appended below after the push — see the bottom of this
file; the fingerprint entry in BUILD_FINGERPRINTS.json carries the release
commit SHA once it exists.)
