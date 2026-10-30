# V10.5.1 — startup regression repair and main integration

## Summary

A V10.5 shared-node-index regression was reproduced on isolated saved-trade fixtures: the cold Overview request took **31.603 seconds**, beyond the existing **15-second** browser deadline. The V10.4 reference took **0.196 s** and the corrected generation-only request **0.175 s**. No timeout was increased, metric replaced with dummy data, dashboard redesigned, or historical calculation reverted to stale engine snapshots.

The fix changes **three production files**. The complete V10.3/V10.4/V10.5 history is integrated into local `main` by fast-forward, with V10.5.1 committed on top. The publication receipt records the final SHA and independently verified remote `main`. GitHub's default branch was checked as `main`; no default-branch change is needed.

**Evidence boundary:** this reproduces the Overview timeout mechanism, not an independently diagnosed native Windows System Status timeout. Both status endpoint implementations and their pollers are unchanged across V10.4, V10.5 and this fix. Status initial load, repeated refresh, temporary-failure retention and Retry Status pass here. Standalone System Status did not time out in the comparison. Native Windows/Python 3.14.6/MT5 and the user's actual database remain unavailable, so a separate native-only status issue cannot be ruled out.

## 1. Reference and Git inspection before modification

- Last pre-V10.5 reference: **V10.4**, `ea58dc9dad6592779c35a78f367bbb37631880d6`.
- Complete V10.5: `85b79b1c4097b026111262e300e442814664db31`, remote `v10.4-throughput`.
- Original remote/default `main`: V10.2, `f5935f335ad53ddc5d329a165821cf367f196000`.
- V10.2 `main` is an ancestor of V10.5; no unrelated divergent main history was found. The fresh authenticated checkout was clean. Existing local user work was not used as an experimental checkout.
- The last-pre-V10.5 commit is a source/reference identification, not a claim that its native startup has been independently certified on the user's PC.
- Read-only comparison worktrees, dependencies, profiles, databases, benchmarks and test logs were placed outside `/home/user`. Git LFS smudging was disabled; native research DB contents were not available or substituted.

## 2. Concise regression comparison

| Area | V10.4 / previously working design | V10.5 difference | V10.5.1 correction |
|---|---|---|---|
| Overview loading, rendering and polling | Existing compact top-node request; generation cells and generation-specific sort keys; serialized refresh and retained last values | Overview itself unchanged; request still defaults to effective metrics | Explicitly request `metric_source=generation`, matching the generation cells already rendered |
| Shared node-index backend | Select lightweight saved scenario headers, then preferred/latest scenario per node | `build_index()` calls authoritative `enrich_many()` on **every** completed/partial historical run **before selecting** one per node | Restore lightweight header selection first; hydrate only the selected run per node through the same authoritative V10.5 service |
| Generation-only index request | Could cheaply read stored header summaries | New all-history work happens unconditionally, even when generation data is requested | Skip the DeepTest projection for an explicitly generation-only request |
| Browser request lifecycle | Existing in-flight GET coalescing, 15-second AbortSignal deadline, fallback AbortController cleanup | No relevant V10.5 change; only unrelated new deletion API methods | Unchanged; no timeout increase, suppression or cancellation redesign |
| App status / top System Status row | Existing `/api/status`, `/api/system/status_row`, in-flight guards, polling, Retry Status, last values | Implementations unchanged | Retained verbatim; remove the unnecessary competing startup workload instead of inventing another health architecture |
| Health/readiness/bootstrap | Existing health/readiness routes and bounded bootstrap | `backend/app/main.py` unchanged | Unchanged; normal isolated ASGI lifespan tested |
| Python signals/alarms | No relevant signal/alarm timeout handler on these request paths | No new Python alarm explains the text | No signal changes; the browser's AbortSignal deadline explains the observed error wording |

### Confirmed root cause

The V10.5 authority was attached at the wrong level of the shared index. A compact Overview request that displays saved generation records unnecessarily read and recalculated the complete DeepTest archive. A cold cache, a larger archive, or normal SQLite revision invalidation makes that work synchronous again. Client abort does not cancel backend financial CPU work, so repeated attempts can add pressure to the shared backend/database.

The service's DB lock was already released before financial CPU work; V10.5.1 does **not** claim the entire financial calculation was incorrectly inside one lock. Snapshot capture still reads full saved trade rows under the shared DB lock, and unnecessary all-history CPU/reads are the demonstrated regression. The corrected request avoids those reads entirely.

A representative fixture with **32 historical PARTIAL runs × 15,277 trades = 488,864 persisted rows** reproduced the cold request exceeding the deadline. These are labelled synthetic regression records, **not** the missing user run or real-market performance. Status calls remained responsive in the standalone comparison; attributing the user's exact System Status message to resource contention is plausible but **not independently proven** without native timing/log evidence.

## 3. Exact production repair

1. `frontend/src/pages/Overview.jsx`: add `metric_source: "generation"` to the existing compact node request. The existing table already renders `GenerationCells`. Search, favourites, sorting, metrics, layout, refresh cadence and last-known-value behavior are unchanged.
2. `backend/app/api/routes.py`: bypass the DeepTest index only when `metric_source == 'generation'`. All effective/DeepTest callers still use the shared authoritative projection. No other route body was changed.
3. `backend/app/stats/deeptest_projection.py`: group the lightweight valid-status headers, choose explicit preferred/latest using the existing precedence, then call V10.5 `enrich_many()` only for the chosen runs. Keep the authoritative raw-trade calculation, PARTIAL/missing-evidence behavior, preferred metadata, valid scenario counts and cache invalidation. Older scenarios remain fully inspectable/comparable on demand.

The original V10.4 node-index source fingerprint test is retained with its **original expected digest**. It now normalizes only the precisely allowlisted three-line generation guard before hashing; every other byte of the historical/Live mapping remains pinned. Three additional frozen Live source files remain hash-pinned unchanged. No assertion/test was removed and no expected fingerprint was simply replaced to bless an arbitrary rewrite.

## 4. V10.5 and prior functionality preserved

Unchanged from V10.5: authoritative metric service and formulas, actual capital/currency rules, filtered trade calculations, chronological drawdown amount/percentage, PARTIAL provenance, >3,000-trade retrieval and complete CSV, cache implementation, scenario assignment snapshots and Live day/session replacement, stale-save protection, persistent Starred Nodes, top-level Delete All Scenarios, selected-node deletion, scoped rollback/tombstones/worker safeguards and Live/shared/unverified-template retention.

Generation/backtest engines, genomes, ancestry, qualification/selection/fitness, database implementation/schema, market parser, MT5 integration, execution/cost conventions, worker limits and V10.4 durable writing optimizations are not changed by V10.5.1. The full prior release history is preserved rather than replacing main with a partial snapshot.

## 5. Targeted and broad validation

### Regression evidence before and after

- New backend regressions against uncorrected V10.5: **6 failed, 2 passed**. After repair: **8 passed**, including selected-only recalculation, generation-only zero trade reads, preferred/implicit/failed/partial/missing preferences, invalidation, missing-trade N/A and responsive status/generation requests while a DeepTest calculation is deliberately held.
- New UI harness against uncorrected V10.5: **7/8 passed**, with the generation-only startup assertion failing. After repair: **8/8 passed**.
- New + V10.5 backend focused run: **42 passed**.

### Overview and System Status UI contracts

All eight new behavior checks pass: Overview initial generation metrics; three consecutive refreshes with unchanged-node/inventory reuse; genuine temporary failure retains metrics and recovers; System Status loads current fixture CPU while optional native MT5/GPU are unavailable; slow status polls do not overlap and stale values recover; initial app status failure supports Retry Status; repeated app-status ticks do not overlap and preserve last lab state; unmount clears polling timers. These are jsdom/HTTP-fixture tests, not native visual certification.

### Same broad backend selection

| Version | Passed | Failed | Errors | Time |
|---|---:|---:|---:|---:|
| V10.4 reference | 369 | 17 | 4 | 75.39 s |
| Published V10.5 | 403 | 17 | 4 | 77.77 s |
| Corrected V10.5.1 | **411** | **17** | **4** | 77.62 s |

**All 21 nonpassing identities exactly match across all three versions; no new failing identities remain.** These existing failures/errors require unavailable historical/real fixture inventory. They are listed in `V10_5_1_VALIDATION.json`, not called passing. One existing TestClient/httpx deprecation warning remains.

All six frontend harnesses pass: new V10.5.1 startup/status, V10.5 integrity, V10.4, V10.3 SPEED LOG, V10.2 dashboard, V9.3. Production frontend build passed in **8.01 s**, with the existing bundle-size warning. `git diff --check` passed.

### Isolated cold/warm comparison

Same source-level operation and same synthetic records, separate fresh DB per version; timings are single comparisons, not universal speed guarantees:

| Operation | V10.4 | V10.5 | V10.5.1 |
|---|---:|---:|---:|
| Cold Overview request | 0.195683 s | **31.602597 s** | **0.175228 s** |
| DeepTest runs calculated for that request | 0 | **32** | **0** |
| Full trade SELECTs for that request | 0 | 4 batched queries | **0** |
| Three following warm Overview requests | 0.008–0.011 s | 0.030–0.033 s | 0.009–0.013 s |
| Selected Deep projection after status activity | 0.000515 s, legacy summary | **32.021394 s, 32 runs** | **1.082185 s, one chosen run** |

The selected authoritative drawdown amount remains **15,289** in V10.5 and V10.5.1 for this synthetic fixture. We do not revert to V10.4's legacy snapshot to make financial results fast. Raw trade counts/P&L and saved legacy header snapshots were verified unchanged throughout each comparison. Three consecutive standalone status/health/readiness cycles remained responsive; maximum individual request was under 0.274 seconds across these route-level comparisons.

### Normal ASGI startup, not just mocked routes

The existing normal startup/bootstrap/shutdown lifecycle was also executed on each source version with a fresh external simulator-only profile, one synthetic node and three closed trades. For **all three versions**, three consecutive cycles of `/health`, `/api/ready`, `/api/status`, `/api/system/status_row` and generation-only `/api/nodes` returned **HTTP 200**. Corrected maximum request time was **0.213 s**. The actual measured context-entry startup time was 0.069 s for the corrected profile, **excluding Python imports, fixture setup and native socket binding**; this is not a claimed Windows launch time. Live trades remained zero; the fixture node and closed rows were preserved. No lab autostart, real terminal, orders or historical campaign was enabled.

This small normal-lifespan fixture also passes in V10.5: it is the larger historical workload that demonstrates the regression, not an assertion that every V10.5 installation must time out.

## 6. Data, workspace and delivery safety

- Before modifications, every regular inherited workspace file outside excluded generated/credential-metadata directories was SHA-256 inventoried: **1,785 files**. Final receipt records the comparison; no inherited DATA/research/protected evidence is intentionally edited or removed.
- DATA Git tree is unchanged from both original main and V10.5: **`68225e768709dcd9fb9123642a5164e4e68e79ab`**. No data file is part of the V10.5.1 patch. Existing native DB/sidecars/backups/failed-preflight evidence were not opened for experimental writes.
- All generated profiles, dependencies, fixture DBs, large logs and benchmarks remain outside the persistent workspace. Only this report, compact validation and publication receipt are saved under `/home/user`; no source/patch/handoff archive is added. Final persistent byte budget is recorded in the receipt.
- The existing `main` history is preserved through a normal fast-forward integration of V10.5 plus one targeted fix commit. No force push, reset, clean, discarded user edits or DATA replacement is used. Remote SHA, ancestry, changed blobs, default branch and DATA tree must be verified before claiming publication.

## 7. Operator verification after publication

A normal fresh clone should now obtain the corrected release directly from the default branch:

```powershell
git clone https://github.com/awais225/evolutionary-trading-lab.git
git -C evolutionary-trading-lab branch --show-current
git -C evolutionary-trading-lab log -1 --oneline
```

Expected branch: `main`; expected title: `V10.5.1 restore dashboard startup and integrate V10.5 into main`. Use the full SHA in the publication receipt. For an existing installation, stop the app and review `git status` before updating. Preserve DATA, DB/WAL/SHM, configuration and unrelated edits; do not use destructive reset/clean or replace your working data with a fresh checkout. Run the normal launcher/build procedure, restart the existing backend, then hard-refresh the browser. Confirm the build-identity chip names the new commit.

On the native installation, check Overview startup/refresh, System Status and Retry Status, and multiple refresh cycles. Browser Network should show Overview `/api/nodes` with `metric_source=generation`, not a cold all-history effective projection. If System Status still times out, record timings and HTTP outcomes separately for `/api/status`, `/api/system/status_row`, `/health` and `/api/ready`, plus the deployed commit identity. That evidence is necessary to diagnose a distinct native/optional-service issue rather than hiding it with a longer timeout. Do not mass-rerun historical tests, alter trading activation, or clear the database as a diagnostic step.
