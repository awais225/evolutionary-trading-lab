# V9.6 — node-generation lock-order halt correction

**2026-10-22. Reproducible source deadlock corrected; genuine 5,000-node completion remains unverified.** This supersedes the earlier evidence-blocked report after receipt of the user's incident log. No regression phase or generation campaign was performed.

## 1. Baseline
Repository `awais225/evolutionary-trading-lab`, branch `main`; freshly verified local HEAD and fetched `origin/main`: `2343931a31dad7f08d9f943e88879f4dc4c2b947`. Missing ephemeral Git metadata was reconstructed without checkout/reset or overwriting surviving files. Initial user changes/status were recorded and preserved. Missing external canonical DATA is not an empty incident database.

## 2. Previous work reviewed
All seven V9.5 final reports and continuation checkpoint; relevant historical halt/generation reports and V9.2 generation-window report; recent lifecycle changes; current frontend target/start/resume → routes → Lab → evolution/candidate/downstream/worker/checkpoint/display paths. Historical V9.5 results and older 413-node fixes are not treated as current incident proof. No historical acceptance suite was rerun.

## 3. Root-cause evidence — recorded before editing
User attachment `LOG.txt` is a 4,000-entry technical export, 12,053 lines, SHA256 `1de07d258167defcb4518fe91900779567e09c849a14dd6ed530504610865508`.
- Line 10536: `Target=5000 Current=2565 Remaining=2435 Generation=5`; line 10551 confirms persisted 2565/5000. Last canonical child is `Node_3352`, not the population count.
- Line 12019: screening finishes, 481 screened / 427 survived / 54 failed / 0 requeued.
- Lines 12038–12050: all three validation calls finish successfully by 16:45:54.605.
- Final line 12053, 16:45:54.610: `Transitioning VALIDATION → QUALIFICATION`. Its next pipeline-stage entry is absent when exported at 16:46:49.675. The earlier identical handoff has both entries (1982–1987).

Current-source lock cycle:
1. `StageManager.transition()` holds the stage RLock, logs that handoff, then calls milestone `set_running()`, requiring the milestone RLock.
2. Milestone `compute_route_state()` held the milestone RLock while obtaining evolution state; that reads `StageManager.current_stage`, requiring the stage RLock.
3. Generation waits for milestone; dashboard polling waits for stage. Reentrancy cannot resolve locks owned by different threads. The same blocked orchestrator cannot reach its next tick/watchdog. Frontend milestone polling calls this route every two seconds.

Before product edits, a bounded isolated diagnostic using the actual stage/milestone classes reproduced both blocked threads, zero errors and no subsequent pipeline-stage event in 1.201 s. Captured stacks show both opposing lock waits. Cause/evidence were recorded separately before editing. This proves the current-source deadlock and matches the incident's precise boundary; the export is not a live Windows thread dump and contains no deployed Git SHA.

## 4. Why 2,565, previously approximately 3,625 / 3,865?
The demonstrated defect depends on concurrent polling/transition timing, not a fixed count. It can stop at different batch boundaries. The new log confirms the backend-reported persisted 2,565/5,000 boundary; it does not conclusively attribute the two older incidents. Canonical ID maximum, research aliases and population counts remain distinct; no historical count is hard-coded into the fix.

## 5. Target definition — unchanged
The engine reads a positive `meta.total_node_target`, then configured evolution target, then its existing fallback. Birth ceiling/remaining count persisted strategy rows for `active_run_id` when set, otherwise all persisted rows. Default ceiling counting includes legacy/excluded rows; the user-facing population snapshot excludes `LEGACY_TEST`/`EXCLUDED` and labels current-run and lifetime scopes. No target/count/qualification rule was changed, and count-scope differences are not asserted as this halt's cause.

## 6. Every changed file and narrow correction
- `backend/app/orchestrator/milestones.py`: read evolution state outside the milestone lock; publish outside all six milestone mutators' lock scopes. Both changes are necessary: merely moving the reader would leave broadcasts running under their callers' outer milestone lock. State mutation/copy remains protected. Same calculations, fallback values, response fields and event types. Source diff: 16 insertions / 15 deletions; no new dependency or interface.
- `V9_6_NODE_GENERATION_HALT_DIAGNOSTIC_REPORT.md`: this necessary diagnostic report.

No changes to StageManager, Lab/worker lifecycle, generation/genomes, validation/fitness/qualification, frontend, trading, risk, identities, storage/configuration or dependencies. Initial DATA deletions, missing LOGS/lab.pid, two MT5 tool mode changes and five untracked V9.5 verification files are unrelated and excluded from staging.

## 7. Minimal verification — explicitly no regression testing
- Changed module compiled in memory; actual stage/milestone modules loaded in an isolated component context.
- Same bounded contention schedule after correction: both threads complete; `QUALIFICATION` reaches the pipeline adapter; evolution snapshots observe milestone lock unowned. Receipt completes in 0.165 s, including direct-call inspection below.
- All six immediate milestone mutators still publish one `milestones_update`, outside their milestone lock.
- Direct existing `get_workflow_milestones` endpoint-function call returns the same eight response keys and seven milestones. This is not an HTTP/ASGI or full-backend check.
- Diagnostics use an in-memory event bus and a snapshot adapter reading the real stage property; persistence/activity are disabled. No app/DB/MT5 initialization, strategy records or generation. The first direct-call diagnostic lacked relative-import globals; correcting only that diagnostic context resolved the setup error.
- Focused diff/whitespace and surviving-file preservation audits. No test files/frameworks, regression suites, full suites, full backtests or MT5 simulations created or run. No broad acceptance claim.

## 8. Generation outcome / last count / 5,000
Last incident log-confirmed persisted count: **2,565**, target **5,000**, remaining **2,435**. Incident SQLite was not independently queried. No controlled generation campaign attempted: canonical runtime/market data are unavailable here, and an expensive reconstructed campaign is not authorized or necessary to prove this lock cycle. **Beyond-stall genuine generation and completion at 5,000 remain unverified.** Neither a RUNNING label nor successful component calls establish those outcomes.

## 9. Protection checks
All originally surviving files except the explicitly changed source/report remain byte-identical; initial unrelated status is preserved. No canonical DATA/SQLite/cache/market/history/configuration restore, write, reset, migration or cleanup. No dummy strategy records, target lowering, validation bypass, hidden errors, auto-generation, trading or orders. Only the two reviewed paths are eligible for staging; raw incident logs/private diagnostic receipts are not committed.

## 10. Local commit
Exact requested message: `fix(v9.6): resolve node generation halt before target`. Local commit on `main` only, after final staging/diff/secrets audit; final SHA supplied separately. No amend/reset/discard/force push.

## 11. Remote / no push
Authenticated remote baseline `refs/heads/main`: `2343931a31dad7f08d9f943e88879f4dc4c2b947`. Final remote verification and local commit identity are supplied with completion. **No push.** The local correction is not represented as deployed to the user's installation.

## 12. Windows / MT5 / deployment limits
Native Windows/Edge/MT5 and the installed build's exact SHA remain unverified. Full incident logs, live stacks and database scope are unavailable. The demonstrated race is thread-lock ordering, not an OS-specific worker algorithm. An already-deadlocked old process cannot be repaired in memory by editing source: after applying the local correction, load it through a controlled backend restart and explicitly resume the existing persisted run, without resetting nodes/data. No such restart/resume was performed here. User-side beyond-stall/5,000 verification remains outstanding.
