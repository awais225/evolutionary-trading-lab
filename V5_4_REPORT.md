# V5.4 — ONE NODE AUTHORITY + MT5 MANUAL EXECUTION · VERIFICATION REPORT

Release **V5.4** · release-code commit **`238e920`** · report updated 2026-10-08 14:10Z (Asia/Karachi)
Release line: `… 9d62a10 → 2fafce7 → 238e920 → fda5c09 → c282e5a → d349693 → 3bca4f7 → 8986322 → f16d567 → c86680f → fccc7d4 → f0f98f4`
Deliverables: `backend/app/research/populations.py` (`node_state_snapshot` +
`AUTHORITATIVE_ALIASES`), `backend/app/stats/research_stats.py` (two-axis
`counter_audit`), `backend/app/mt5/execution.py` + `backend/app/mt5/mt5_real.py`
(result contract, ONE send site), `backend/tools/mt5_demo_forensics.py` +
`CHECK_DEMO_FORENSICS.bat` (pre-send exposure inventory, forensic report),
`tests/test_v5_4_node_authority.py` (15 tests),
`tests/test_v5_4_order_check_result_is_not_execution.py` (8 tests),
`tests/test_v5_3_forensics_tool_report.py` (14 tests),
`/home/user/v54_evidence.txt` (raw live evidence),
`dashboard_preview/DASHBOARD_PREVIEW.html` (this build, live in the workspace),
this report.

---

## 1 · BUILD

| fact | value | how it was proven |
|---|---|---|
| Git SHA (checkout) | **`f0f98f4`** (HEAD) — `c86680f` is the last backend-code commit of V5.4 | `git log --oneline -1` |
| Git SHA (GitHub) | **`f0f98f4`** — `refs/heads/main` | `git ls-remote origin refs/heads/main` → `f0f98f4e0da061e988954cb34bf07d61ce499d22`; push logs `3bca4f7..8986322`, `8986322..f16d567`, `f16d567..c86680f`, `c86680f..fccc7d4`, `fccc7d4..f0f98f4` |
| Runtime Git SHA | **`f0f98f4`** — backend restarted AFTER the last push | `GET /system/build → git_commit` |
| Running process | pid **12918** (backend process of workspace pid 12914), `python -m uvicorn app.main:app --host 0.0.0.0 --port 8787` | live listener; `/system/build → process.pid` |
| Serving | `static` from `frontend/dist` | `/system/build → serving` |
| Frontend bundle | `dist_status = FRESH`, `dist_matches_src = true` | `/system/build`; `frontend_build.write_stamp` + `check_dist` |
| Served source hash | `src_hash 7e46d547d96b4b71…` (61 files) | equals the published V5.4 fingerprint |
| Served index | `index_sha256 0a320fc0e4087504…`, entry assets `assets/index-CtPUGZPn.js`, `assets/index-DG45pVtK.css` | equals the published V5.4 fingerprint |
| Published identity | `BUILD_FINGERPRINTS.json` entry **V5.4** — commit `c86680f`, recorded `2026-10-08T14:04:11Z` | `build_fingerprints.py --match` → `FINGERPRINT_MATCH: V5.4`; regression `test_12` (green in the final suite) |
| Workspace preview | `dashboard_preview/DASHBOARD_PREVIEW.html` (4,921,018 B) rebuilt from **this** commit at `2026-10-08T14:05:08Z` — release V5.4, 83 recorded endpoints, 0 unavailable, `PREVIEW_RENDER: PASS`, 0 console errors, `V54_UI_AUTHORITY: PASS` | `record_demo_data.py` + `build_preview.py` + `verify_preview.mjs` + `v54_pages_probe.mjs` |
| Product release label | **`V5.4`** (`app/versions.py PRODUCT_RELEASE`) | `/system/build → release` |
| Backend code hash | `code_hash cadbdc2cff9239ec5091a26be3235b931c429cbd6e3f3274005561aaeeb5878b` (111 `backend/app` files) | `/system/build → process.code_hash` |
| Served runtime counters | `DEAD 10010 / QUALIFIED 43 / ALIVE 46` — identical from `/api/status`, `/api/nodes/populations`, `/api/lab/status`, `/api/stats/overview` **and** from the legacy alias keys the Population KPI row and the Live Activity panel print | recorded preview data + live sweep (`/home/user/v54_evidence.txt`) |

**BUILD = PASS.**

---

## 2 · MT5 DEMO

**FINAL = BLOCKED — device-local.** This workspace is Linux; `MetaTrader5` is not
importable here, so no DEMO order can be executed or claimed from here. The
implementation defect that produced the operator's failure is **fixed and
test-verified**; the real-Windows acceptance (§2.3) is the only remaining step.

### 2.1 The operator's failure, re-derived on the real code path

Reported: BUY 0.03 XAUUSD, entry 4125.73 / SL 4095.73 / TP 4185.73, `retcode null`,
`comment ""`, no result object. Re-derivation (scratch reproducer
`/home/user/v54_probe_issue2.py`, never committed) drove the real
`place_demo_order` → `MT5RealBridge.send_market_order` chain against a fake
terminal and mapped **three** defects:

| # | defect (pre-fix behaviour) | why it produced the operator's symptom |
|---|---|---|
| 1 | a **refused preflight** returned the *check's* retcode in the order's `raw` slot, with `order_send.outcome = ORDER_SEND_RETURNED_RESULT` while `called: false` | a check answer was presented as an execution answer |
| 2 | an `OrderCheckResult` returned **by `order_send`** was read as "Unrecognised MT5 retcode 0" | the broker's non-execution answer had no name |
| 3 | the **check-raise** path ("could not be evaluated") is the operator's exact path | a `None` retcode plus an empty comment was the whole story — no phase, no `last_error`, no exception text |

Post-fix map (all six terminal behaviours, same scratch reproducer):

| terminal behaviour | `result_class` | detail / phase | send called |
|---|---|---|---|
| `order_send` ok (10009) | `EXECUTED` | `BROKER_RESULT`, ticket 555001 | yes |
| `order_check` refuses (10030) | `CHECK_FAILED` | "ORDER NOT SENT — mt5.order_check REFUSED THE REQUEST" | **no** |
| `order_check` raises | `CHECK_ERROR` | "… COULD NOT BE EVALUATED" (**the operator's path**) | no |
| `order_send` → `None` | `UNKNOWN_EXECUTION` | phase `ORDER_SEND_RETURNED_NONE` + `last_error` | yes (once) |
| `order_send` → `OrderCheckResult` | `UNKNOWN_EXECUTION` | phase `ORDER_SEND_RETURNED_CHECK_RESULT` | yes (once) |
| `order_send` raises | `MT5_EXCEPTION` | exception type + message preserved | yes (once) |

Contract: `result_class ∈ {EXECUTED, SEND_FAILED, CHECK_FAILED, CHECK_ERROR,
UNKNOWN_EXECUTION}`; `result_class_detail ∈ {BROKER_RESULT, NO_RESULT,
NO_USABLE_RESULT, CHECK_RESULT_RETURNED}`; the bridge phase lives **only** under
`order_send.phase ∈ {NOT_CALLED, ORDER_CHECK_REFUSED, ORDER_CHECK_ERROR,
BRIDGE_NOT_CONNECTED, ORDER_SESSION_UNAVAILABLE, ORDER_SEND_RETURNED_RESULT,
ORDER_SEND_RETURNED_NONE, ORDER_SEND_UNUSABLE_RESULT,
ORDER_SEND_RETURNED_CHECK_RESULT, ORDER_SEND_EXCEPTION}`. Every response carries
the flat contract fields (`broker_retcode`, `broker_comment`, `ticket`,
`fill_price`, `sl/tp_requested` + `_broker`, `order_check_called/passed`,
`order_send_called/phase`, `safe_to_retry`, `nothing_sent`). `safe_to_retry` is
`false` on every ambiguous outcome; nothing is ever resent.

Preserved V5.3 guarantees (unchanged code): one send site, `SESSION_LOCK`,
derived filling mode, `order_check` retcode 0 = CHECK PASSED ≠ acceptance,
post-send verification via `positions_get`/`orders_get`, one click = ONE
`order_send`, deterministic V5.3 magic + client order ID, no auto-retry, no
simulator fallback for real execution.

**ONE send site, proven (not asserted):** the whole backend used to contain a
second `mt5.order_send()` caller — `real_market_order()` with a hard-coded filling
mode, no session check, no forensics and no post-send verification — so an order
could leave the process through a path with no evidence. It now builds the same
request and delegates to `send_market_order()`; `test_07` walks the AST of every
module under `backend/app` and fails if a second executable `.order_send(` call
site ever appears (today: exactly one, `backend/app/mt5/mt5_real.py`).

**Never resend into an open position (V5.4 §2 item 4):** before ANY manual test
the operator's brief requires inspecting the terminal for the previous attempt.
That is now a mechanism, not advice. `backend/tools/mt5_demo_forensics.py`
(`CHECK_DEMO_FORENSICS.bat`) reads `positions_get`/`orders_get` for the symbol,
filtered by magic **777000** (manual) and **777900** (forensic), prints the result
as its own `EXISTING EXPOSURE` report section on every run, and — while such
exposure exists — **refuses to send**: exit **6**, `REFUSED_BEFORE_SEND`,
`result_class EXISTING_EXPOSURE`, `result_class_detail NOT_SENT`,
`order_send.called false`, `safe_to_retry false`, zero transmitted requests, and
the named tickets with instructions to look at Trade/History first. `--force`
(`FORCE_DEMO_ORDER=1`) is the operator's explicit overrule and still performs
exactly ONE `order_send` — it is not a retry. A refusal writes the same
`.json`/`.txt` report artifacts as a normal run, because an unexecuted order is
evidence too.

### 2.2 Live-path honesty on this host (measured)

```
place_demo_order({'symbol':'XAUUSD','side':'BUY','lots':0.01,'confirm':'PLACE_DEMO_ORDER'})
→ MT5ExecutionError MT5_UNAVAILABLE @ stage ACCOUNT_SAFETY:
  "the active market bridge is 'SIMULATOR', not a real MetaTrader 5 terminal"
```
`/api/mt5-execution/preview` (read-only) answers `read_only`-style validation
errors and never sends. **No DEMO order is claimed from this workspace.**

### 2.3 Required acceptance on the operator's Windows machine

1. `CHECK_RUNNING_DASHBOARD.bat` — keep it open: it proves which build runs (cwd,
   HEAD, origin/main, backend PID + python, src/dist fingerprints, build stamp,
   token, start time, port, `/health`, `/system/build`, `changed_since_start`,
   guard verdict). Expected on this release: HEAD/`origin/main` = the build you
   pulled, release `V5.4`, `dist FRESH`, `dist_matches_src true`.
2. `CHECK_DEMO_FORENSICS.bat` (read-only) → expect `ORDER CHECK: PASSED` **and** the
   `EXISTING EXPOSURE` section. **If it reports an open position/order for
   XAUUSD with magic 777000, do NOT send anything** — inspect Trade/History first
   (that is the earlier attempt; the tool itself would refuse with exit 6).
3. `SEND_DEMO_ORDER=1` → **ONE** 0.01 XAUUSD DEMO order, no retry. (Only after
   step 2 reports no existing exposure; `FORCE_DEMO_ORDER=1` overrules the refusal
   knowingly and still sends exactly one order.)
4. Verify the position **in the terminal** (`positions_get`/`orders_get`) and
   attach `LOGS\MT5_DEMO_FORENSICS_<stamp>.txt`.
5. If it fails, the report now names the exact layer (phase + `last_error` +
   request + session snapshot) instead of a bare `retcode null`.

---

## 3 · LIVE TESTING

**PASS (population) / BLOCKED (execution, device-local).**

* Live Testing's node table reads the **same** authoritative population as
  Overview: `/api/live-testing/nodes-table → total 43` = `LIVE_TESTING_ELIGIBLE 43`
  (raw: `/home/user/v54_evidence.txt`), and the workspace preview re-checked it on
  this build: `live-testing table total: 43 | authority LIVE_TESTING_ELIGIBLE: 43 |
  equal: true` (`v54_pages_probe.mjs`, recorded 2026-10-08T14:05:08Z).
* `starred_only` at the contract: omitted → 43, `false` → 43, `true` → 0
  (a truthful empty state, because no node is starred). No 422 for any variant.
* `/api/live-testing/nodes` (ENROLLED/ACTIVE) = 0 with **16** excluded rows
  listed: capability (`LIVE_TESTING_ELIGIBLE 43`) and enrolment
  (`LIVE_TESTING_ACTIVE 0`) are reported separately — nothing is faked behind an
  empty table.
* XAUUSD-only / regime filters untouched; regime labels remain backend-owned.

---

## 4 · STUDY RESET (“Delete all previous data”)

**PASS (by test + code evidence; the live DATA was never reset in this workspace).**

* `tests/test_v5_3_study_reset_identity.py` (10 tests, green in the 851-pass run):
  delete-all resets every persistent identity source, the allocator is lowered in
  the same transaction, `next_node_number: 1`, restart-safe.
* `identity_allocator = {sequence_before, sequence_after,
  next_strategy_id_after_reset, legacy_id_ceiling: 787, next_node_number: 1}`;
  the allocator is never lowered below the legacy id band.
* Documentation of study/run id vs node identity is in `lmsarena.txt` §E/F.
* The live population in this workspace (10,835 stored nodes) was **not** deleted,
  reset or regenerated at any point in this wave.

---

## 5 · TESTS

```
EVOLUTIONARY_LAB_DATA_ROOT=/opt/lmsarena-storage/DATA \
/opt/lmsarena-storage/venv/bin/python -m pytest tests/ -q
→ 865 passed / 0 failed / 1 warning in 176.02 s       (V5.3.1 baseline: 839)
```

| suite | result |
|---|---|
| `tests/test_v5_4_node_authority.py` | 15 passed — partition invariants, one snapshot on every surface, one payload shape, no engine re-binding, run-id adoption rule, two-database isolation, two-axis audit, definitions shipped, Overview source guard, no re-adding of counters, **alias keys == the authority (test_14), Live Activity task state == the authority (test_15)** |
| `tests/test_v5_4_order_check_result_is_not_execution.py` | 8 passed — no EXECUTED verdict from a passing `order_check`, the flat+nested contract, ONE executable `order_send` call site (AST walk), `real_market_order` delegation, no auto-retry |
| `tests/test_v5_3_forensics_tool_report.py` | 14 passed — includes the pre-send exposure inventory: an open magic-777000 position makes the tool refuse (rc 6, nothing transmitted) and `--force` sends exactly one |
| 7-file MT5 set (V5.4 Issue 2) | 163 passed / 0 failed (20.1 s) |
| V4/V5 repaired assertions (7 files) | green — the old assertions pinned the *disagreeing* definitions and the pre-fix check-refund contract; each repair is commented at the assertion |
| `tests/test_v5_2_3_one_table_and_zip_identity.py` | 16 passed — the tree matches the published V5.4 fingerprint |
| final run on the pushed tree | **865 passed / 0 failed** (176.02 s) — includes the fingerprint self-consistency gate (`/tmp/full_v54_l.txt`) |

Repairs were targeted, never blanket-relaxed: the definitions changed on purpose,
so the assertion was rewritten to the new, stricter contract (e.g. the query
budget test now ALSO proves the count does not scale with rows).

---

## 6 · FINAL

| gate | verdict | evidence |
|---|---|---|
| **BUILD** | **PASS** | §1 — HEAD `f0f98f4` = `origin/main`, runtime = that commit, dist FRESH (`dist_matches_src true`), fingerprint V5.4 @ `c86680f` re-recorded and matching, preview rebuilt from this commit against the live backend |
| **MT5 DEMO** | **BLOCKED (device-local)** | §2.1 defect fixed + 163-test proof; §2.3 Windows acceptance outstanding — this workspace cannot reach a terminal |
| **LIVE TESTING** | **PASS** | §3 — table 43 = `LIVE_TESTING_ELIGIBLE 43`, all `starred_only` variants 200; the alias fix now also makes the Population KPI row and the Live Activity DEAD/QUALIFIED rows print the authority's numbers |
| **STUDY RESET** | **PASS** | §4 — 10 green tests; live DATA untouched |
| **TESTS** | **PASS** | §5 — 865 passed / 0 failed (176.02 s) |
| **FINAL** | **PASS with one device-local blocker** | one node authority now holds for *every* surface including the legacy alias keys a panel prints (live + recorded preview proof); MT5 order path has ONE instrumented send site and the forensic tool refuses to resend into existing exposure; the only unproven link is a real DEMO fill, which requires the operator's Windows terminal |

The single most important requirement — *stop patching blindly, prove exactly
where the order disappears* — is answered in §2.1: pre-fix, a **refused or
unevaluable `order_check`** was returned as if it were an execution result
(`raw` = the check, `retcode` = the check's retcode, no phase), and an
`OrderCheckResult` from `order_send` had no name. Both are now impossible: the
check travels under `check`, the send result under `raw`, and every outcome has
one stable kind plus a bridge phase.

### Follow-up found while reading the live payload (not while reading code)

The audit of the shipped build showed the alias keys a *different* pair of widgets
print were still fed by the engine's raw-status formula:

| surface | key | value | consumer |
|---|---|---|---|
| `/api/lab/status` | `dead_nodes` / `qualified_nodes` | **9991 / 5** | Population page KPI row (`PopulationSummary.jsx:47-48`) |
| `/api/status.stage_state` | `dead_nodes` / `qualified_nodes` | **9991 / 5** | Live Activity sidebar (`LiveActivitySidebar.jsx:451,454`) |
| authority (`node_state_snapshot`) | `DEAD` / `QUALIFIED` | **9996 / 41** | Overview, Stats, Live Testing, the NODES strip |

Both aliases now resolve from the one snapshot (`AUTHORITATIVE_ALIASES` +
`authoritative_aliases()` in `app/research/populations.py`, applied in
`Lab.status()` and `get_unified_task_state()`), and the engine's raw values stay
observable as `engine_dead_nodes` / `engine_qualified_nodes` / … — nothing is
deleted and nothing is renamed in a way that hides the other axis. `current_nodes`
is deliberately **not** repointed: it is the experiment-scope count, already
published as `experiment_nodes` with `progress_scope: "current experiment"`.

Verified on the live build and in the recorded preview data:

```
authority        TOTAL 10843  ALIVE 46  DEAD 10010  QUALIFIED 43
lab.dead_nodes   10010   lab.qualified_nodes   43   lab.alive_nodes   46
stage_state      10010                       43                     46
overview.pop.dead 10010  overview.pop.qualified 43
raw engine axis  engine_dead_nodes 10003   engine_qualified_nodes 5
EVERY ALIAS == AUTHORITY in the recorded preview data: True
```

Cost, measured in-process: `populations()` 227–278 ms, `node_state_snapshot()`
233–266 ms ⇒ one extra ~0.25 s read per status poll. No cache was introduced — a
cached counter could print a number the authority no longer states.

### One authoritative source — the numbers, verbatim

```
2026-10-08T14:05:08Z   (recorded preview data + /home/user/v54_evidence.txt)
TOTAL 10843 | ALIVE 46 | DEAD 10010 | QUALIFIED 43
LIVE_TESTING_ELIGIBLE 43 | DEEP_TESTING_ELIGIBLE 43 | FINAL_TESTING_ELIGIBLE 0
LIVE_TESTING_ACTIVE 1 | BACKTESTING 0 | VALIDATING 0 | CURRENT_GENERATION 39
partition  46 + 10010 + 787 = 10843 ✓
progress   run RUN-20261005-055251 · nodes 10056 / target 10843 · 787 remaining (92.7%)
identical on /api/status == /api/nodes/populations == /api/lab/status
       == /api/stats/overview.population, and on the legacy alias keys
       (lab.dead_nodes / lab.qualified_nodes / stage_state.*)
authority  app.research.populations.node_state_snapshot
classifier app.research.populations (classifier app.status.node_bucket)
```

(One earlier sweep line printed `False` for a surface comparison; that was my own
extraction reading top-level keys instead of `node_state.state`. The evidence file
carries the note and the corrected, re-verified result — no defect was recorded as
a pass and no pass as a defect.)

The live lab is still generating (TOTAL moved 10,787 → 10,851 during this wave);
the invariants hold on every read, which is what "one authority" now means. The
counts above are the snapshot at `2026-10-08T14:05:08Z`; the live deltas since
then (TOTAL 10,851 / ALIVE 48 / DEAD 10016 / QUALIFIED 45) were verified to keep
the same equality on every surface, including the alias keys.

---

## 7 · V5.4 ACCEPTANCE CHECKLIST (the brief's 17 items, answered with evidence)

Written `2026-10-08T14:40:35Z`. Every number below was read from the running build
(`pid 2206`, commit `f0f98f4`, code hash
`cadbdc2cff9239ec…`, `dist FRESH`, `dist_matches_src
True`); the raw sweep is in `/home/user/v54_evidence.txt`.

**1 · Root cause of the Overview node-count mismatch — three derivations, one database.**

| where | what it counted | why it disagreed |
|---|---|---|
| the authority (`app.research.populations.node_state_snapshot`) | every stored strategy row, classified once (`app.status.node_bucket`) | the correct answer: TOTAL 10795 / ALIVE 36 / DEAD 9972 / QUALIFIED 33 |
| Overview PROGRESS | the engine's **run-scoped** count (`current_nodes`, the experiment ceiling) plus the raw-status formula | printed the experiment ceiling as "TOTAL NODES" → `10000 / 10000`, and QUALIFIED from the raw status vocabulary → `5` |
| the legacy alias keys (`lab.dead_nodes`, `lab.qualified_nodes`, `stage_state.*`) | the engine's raw-status formula | fed the Population KPI row and the Live Activity DEAD/QUALIFIED rows → `9991 / 5` vs the authority's `9996 / 41` on the live DB |

The 10,000 was never a total of anything; it is the *current experiment's* target
(now published under its own name, `progress.scope = "current experiment"`,
`experiment_nodes`). The 5 was QUALIFIED counted from stored status strings
instead of the research classifier.

**2 · The authoritative source.** `app.research.populations.node_state_snapshot(db, engine)`
— one read of the strategy rows, one classifier, one state vocabulary. Served at
`/api/status.node_state`, `/api/nodes/populations` (`counts`+`state`),
`/api/lab/status` (node_state + flat keys + legacy aliases),
`/api/stats/overview.population`, `/api/research/facets.population`,
`/api/nodes/deep-testing/state.populations`, and the Live Testing node table.

**3 · Files changed for node consistency.** `backend/app/research/populations.py`
(`node_state_snapshot`, `AUTHORITATIVE_ALIASES`, `authoritative_aliases`),
`backend/app/orchestrator/lab.py` (`status()` — aliases follow the snapshot),
`backend/app/orchestrator/stages.py` (`get_unified_task_state()` — the Live
Activity source), `backend/app/stats/research_stats.py` (two-axis audit),
`backend/app/api/routes.py` (endpoints publish the snapshot), and the frontend
pages/components that now read it instead of recounting
(`Overview.jsx`, `Stats.jsx`, `LiveTestingPanels.jsx`, `NodePopulationStrip.jsx`,
`PopulationSummary.jsx`, `LiveActivitySidebar.jsx`).

**4 · Overview, Deep Testing, Live Testing, NODES and PROGRESS now use the same
authority.** Measured on this build:

```
authority                TOTAL 10795 | ALIVE 36 | DEAD 9972 | BACKTESTING 0 | VALIDATING 0
                         | QUALIFIED 33 | FINAL 0 | DEEP 33 | LIVE 33
                         | LIVE ACTIVE 0 | GEN 39
/api/status.node_state                     IDENTICAL
/api/lab/status.node_state                 IDENTICAL
/api/lab/status flat keys                  IDENTICAL
/api/lab/status legacy aliases             IDENTICAL
/api/status.stage_state (Live Activity)    IDENTICAL
/api/stats/overview.population             IDENTICAL
/api/research/facets.population            IDENTICAL
/api/nodes/deep-testing/state.populations  10795 / 36 / 33 / DEEP 33
/api/live-testing/nodes-table              total 33 = LIVE_TESTING_ELIGIBLE 33
partition                  36 + 9972 + 787 = 10795  (TOTAL 10795)
workspace preview probe    V54_UI_AUTHORITY: PASS — live-testing table 33 == authority 33, ceiling-as-total: false
```

The Live Testing table returned exactly 33 rows for
33 eligible nodes (page-size 33 = population 33; the API
reports `total`, never a fabricated count).

**5 · Root cause of the `OrderCheckResult` problem.** `place_demo_order()` →
`MT5RealBridge.send_market_order()` calls `mt5.order_check()` and then
`mt5.order_send()`, and the pre-fix normaliser read **whatever object came back
from the send slot**. When that object was an `OrderCheckResult`
(`retcode=0, comment='Done'`) it was treated as a broker execution answer with
retcode 0 → the payload carried `retcode null` + `status UNKNOWN` +
`result_class NO_USABLE_RESULT`, i.e. a **passing preflight was reported as an
unusable execution**, and the fact that no order had been transmitted was lost.
An `order_check` result is a preflight answer and can never be an execution
answer; retcode 0 there means CHECK PASSED, not EXECUTED.

**6 · MT5 files changed.** `backend/app/mt5/mt5_real.py` (the ONLY executable
`order_send` call site; `_resolve_request_filling`, `_ensure_order_session`,
`check_market_order`, `send_market_order` with phase-tagged forensics,
`verify_sent_order`, `_result_to_dict`, `_last_error_safe`; `real_market_order`
now delegates), `backend/app/mt5/execution.py` (`RESULT_CLASSES`,
`_CONTRACT_FIELDS`/`contract_fields`, `place_demo_order`, `_bridge_phase`),
`backend/app/mt5/order_semantics.py` (`interpret_check_result`),
`backend/tools/mt5_demo_forensics.py` + `CHECK_DEMO_FORENSICS.bat` (the
instrument the operator runs), `backend/app/mt5/runtime_report.py` (status
honesty).

**7 · `order_check()` and `order_send()` are separate phases.** Refused check →
`CHECK_FAILED` (send **not** called, phase `ORDER_CHECK_REFUSED`); failed check
call → `CHECK_ERROR` (phase `ORDER_CHECK_ERROR`); passing check → the send is
attempted and the answer is classified from the send slot only:
`EXECUTED` / `SEND_FAILED` / `UNKNOWN_EXECUTION`, with `order_send.phase ∈ {NOT_CALLED, ORDER_SEND_RETURNED_RESULT, ORDER_SEND_RETURNED_NONE, ORDER_SEND_UNUSABLE_RESULT, ORDER_SEND_RETURNED_CHECK_RESULT, ORDER_SEND_EXCEPTION}`.
A check result arriving in the send slot is named
`ORDER_SEND_RETURNED_CHECK_RESULT` (`result_class_detail CHECK_RESULT_RETURNED`) —
the operator's exact case.

**8 · `order_send()` really is called after a successful validation.** Proven by
`tests/test_v5_3_demo_execution_forensics.py::test_01` and
`tests/test_v5_3_forensics_tool_report.py::test_06` — both assert
`order_check_calls >= 2` (the filling probe + the request check) **and then**
`order_send_calls == 1` with `sent_requests == 1`; the pre-fix code could stop at
the check. `tests/test_v5_4_order_check_result_is_not_execution.py::test_07`
walks the AST of every module under `backend/app` and fails if a second
executable `.order_send(` call site ever appears (today: exactly one).

**9 · The actual `order_send()` result is returned.** The send slot's own object
is captured verbatim (`result_type`, `result_repr`, retcode, deal, order,
volume, price, comment, `request_id`) and normalised into the response contract:
`ok`, `status`, `result_class`, `result_class_detail`, `retcode`, `ticket`,
`order`, `deal`, `position`, `fill_price`, `volume`, `requested_sl` /
`broker_sl`, `requested_tp` / `broker_tp`, `broker_comment`, `broker_message`,
`client_order_id`, `order_check_called`, `order_send_called`, `order_send_phase`,
plus the nested `contract` block. A `None` from `order_send` reports the binding's
own `mt5.last_error()` (before *and* after), never a generic UNKNOWN.

**10 · Actual retcode from a controlled test.** On **this** host the bridge is
`SIMULATOR` (MetaTrader5 is not importable on Linux) and `place_demo_order`
correctly refuses with `MT5_UNAVAILABLE @ ACCOUNT_SAFETY` — **no real retcode can
be produced or claimed here.** Against the instrumented fake terminal the send
path returns `retcode 10009` (`DONE`) once the check passes. The real, controlled
DEMO test is step 3 of §2.3 and must run on the Windows machine.

**11 · Ticket/order/deal/fill from the controlled test.** Same status: the
simulated proof produced order ticket `555001` / deal `666001` / fill price =
request price / volume `0.03`; the real values come from
`LOGS\MT5_DEMO_FORENSICS_<stamp>.txt` on the operator's machine (`order_send`
result + post-send `positions_get`/`orders_get`).

**12 · No automatic retry was added.** One click = ONE `order_send`
(`SESSION_LOCK`, duplicate `client_order_id` gate); `safe_to_retry` is `false` on
every ambiguous outcome; `UNKNOWN_EXECUTION` stays UNKNOWN until the terminal is
inspected; the forensic tool now refuses to send at all while an open position /
order with magic 777000 or 777900 exists (exit 6, nothing transmitted) unless the
operator overrules with `--force`, which still sends exactly one order.

**13 · `npm install` / `npm run build`.** Both succeeded on this checkout:

```
npm install            → up to date (104 packages, no change to package.json/lock)
npm run build          → ✓ built in 5.33s
dist/assets/index-CtPUGZPn.js   629.64 kB   ← identical to the published fingerprint
sha256(frontend/dist/index.html) 0a320fc0e4087504182b100ea38e4c087ee1f633942ae58fab96fd9a2b8f303d  ✓
build_fingerprints.py --match → FINGERPRINT_MATCH: V5.4 (commit c86680f)
```

The production bundle is byte-identical to the published V5.4 fingerprint, so a
stale-bundle mismatch is impossible. The dist stamp was (re)written the normal way,
`backend/tools/frontend_build_guard.py --stamp` — the same tool `Prerequisite.bat`
runs — `built_at 2026-10-08T14:35:41Z`, `git_head f0f98f4`.

**14 · Backend `/health`.** `status ok`, `ready True`;
`/system/build` → release `V5.4`, `git_commit f0f98f4`,
`dist FRESH`, `dist_matches_src True`. MT5 on this
host: `SIMULATOR` (explicitly reported). The three MT5 facts the brief asks for —
`connected true`, `trade_allowed true`, `tradeapi_disabled false` — are printed by
`CHECK_MT5_WINDOWS.bat` / `CHECK_DEMO_FORENSICS.bat` on the Windows machine; this
workspace must not claim them.

**15 · `git status`.** Only the known machine-local drift —
`DATA/DATABASE/lab_state.db-shm`, `LOGS/lab.pid`, mode-only
`backend/tools/mt5_{runtime,windows}_diagnostic.py`, and untracked `DATA/BACKUPS/*`,
`DATA/MISSING/`, `DATA/RESEARCH/mt5_historical/`, `DATA/logs/V3_2_STARTUP_*.log`.
No source file is dirty; nothing machine-local, no `node_modules`, no `.venv`, no
secrets are committed.

**16 · Commit hashes.** V5.4 was delivered as focused commits (not one blob):

```
f0f98f4 docs: chatgpt.txt entry 8 — one authority means one derivation
fccc7d4 chore: record the V5.4 fingerprints at c86680f
366d16c docs: V5.4 follow-up — the legacy *_nodes aliases follow the ONE snapshot
c86680f fix: V5.4 the legacy *_nodes aliases follow the ONE snapshot (third derivation removed)
f16d567 docs: V5.4 pre-send exposure inventory recorded (lmsarena.txt §I, chatgpt.txt entry 7) + fingerprints @8986322
8986322 fix: V5.4 inspect MT5 exposure before any send — never resend a filled order blindly
```

`238e920` release code · `c282e5a` check-result layer · `d349693` ONE send site ·
`c86680f` alias/authority fix · `8986322` pre-send exposure inventory · the
remaining entries are the release records (docs, fingerprints, this report).

**17 · Pushed to `origin/main`.** Yes — `git ls-remote origin refs/heads/main`
equals the local HEAD and `git rev-list --count origin/main..HEAD` is `0`.

### What remains — device-local only

The controlled DEMO trade (items 10–11) needs the operator's Windows machine:
`CHECK_RUNNING_DASHBOARD.bat` (proves the build), then `CHECK_DEMO_FORENSICS.bat`
read-only (**expect `ORDER CHECK: PASSED` and the `EXISTING EXPOSURE` section** —
if it shows an open XAUUSD position for magic 777000, inspect Trade/History and do
not send), then `SEND_DEMO_ORDER=1` for ONE 0.01 XAUUSD order and verify it in the
terminal. The report the tool writes
(`LOGS\MT5_DEMO_FORENSICS_<stamp>.txt`) contains the real `order_send` result.
