# ISSUE-056 — AUTOMATIC MT5 CONNECTION, BROKER METADATA RETRIEVAL, AND MARKET DATA FETCH/PERSISTENCE RELIABILITY

> Archived per repository rule (exact user prompt, summarized faithfully at
> issue opening; the full text lives in the conversation record).

User brief (condensed without changing requirements):

1. **Automatic MT5 connection on startup** — detect whether MT5 is already
   running; determine the running terminal/account from actual terminal and
   account information via the MT5 API, not mere process presence. Exactly
   one valid terminal/account → connect automatically. Multiple → do not
   select arbitrarily; existing dropdown remains for manual choice. MT5 not
   running → do not silently launch an arbitrary terminal; clear
   disconnected status + manual workflow. Auto-connect failure → actual
   reason + manual retry. No duplicate initialization/processes/competing
   connections. Background bounded retry if MT5 starts after the app.
   **Do not redesign/remove/relocate/rename/replace the existing dropdown.**
2. **Automatic broker metadata retrieval** — immediately after successful
   connection retrieve metadata for the ACTUAL connected account into the
   existing Broker Metadata area; refresh on connect/account
   change/terminal change/reconnect; async, deduplicated; never present a
   previous account's metadata as current (stale only if labelled stale with
   reason); real fields only; manual Refresh stays as retry.
3. **Fetch/persistence investigation** — trace all stages with timings from
   LOG; prove persistence (inserted counts vs read-back, commit, integrity,
   restart, idempotency, no false success on failed commit); preserve the 15
   unresolved conflicts (stored preserved, Keep Stored / Accept MT5 kept);
   correct availability so a Saturday fetch ending Friday evening is NOT
   "MT5 fetch required" (measured broker sessions/closures), while genuine
   missing completed candles still report; gaps stay
   closure-vs-unverified, estimates verified, nothing fabricated.
4. **Tests** for one-terminal auto-connect, no-terminal disconnected,
   multiple-terminal ambiguity, account switch refresh, reconnect,
   metadata failure honesty, fetch idempotency, read-back verification,
   failed-commit honesty, conflict preservation, weekend freshness, restart
   durability; ISSUE-054/055 features as regression.
5. **Delivery** — update CHATGPT.txt, CHANGELOG.md, ISSUES_AND_FIXES,
   IMPLEMENTATION_PROGRESS, ARCHITECTURE (if changed), WINDOWS_SETUP (if
   changed), this prompt archive, TROUBLESHOOTING.txt if relevant; run all
   tests + production build; commit and push origin/main; final report with
   12 enumerated items; never claim Windows verification not performed.

---

## OUTCOME (ISSUE-056, LMS environment; Windows genuine-MT5 NOT VERIFIED)

- `decide_auto_connect()` (pure, injectable planner) + background worker
  with bounded retries (0/3/8/20/45s, daemon thread, stops if the user
  connects manually, never launches a terminal, attach-only). Lifespan
  starts it; failures publish the real reason to LOG.
- One shared terminal-refresh core (`refresh_from_terminal`) used by the
  manual endpoint AND an automatic, deduplicated post-connect refresh for
  every registered instrument (fires on manual AND automatic connect).
  Stored snapshots are annotated STALE when the live account's server
  differs. Dropdown/manual switching untouched.
- Fetch persistence: post-commit read-back verification from a fresh
  connection (row-count delta + newest-candle OHLC spot check) as a new
  `POST_COMMIT_VERIFICATION` stage; a mismatch fails the timeframe and
  reports appended=0 instead of a false success. Verified idempotent +
  restart-durable + rollback-safe by tests.
- Freshness: expected-latest-completed-candle now uses the measured
  session model (weekend closure, Friday-evening close, Sunday 23:00 UTC
  reopen, Mon-Thu 21:00-22:00 UTC nightly break — break hour measured from
  stored data: last bar 20:59 UTC, 58/59 breaks), so the owner's Saturday
  fetch ending Friday 23:56 is CURRENT, while genuinely missing completed
  candles still count. UI gains "Market closed — stored data is current".
- 15 conflicts: untouched, still UNRESOLVED (owner's Windows DB).
- Backend 242 passed / 1 skipped; frontend 66/66; production build clean.
- Windows acceptance (genuine auto-connect to a running terminal, live
  metadata retrieval, Saturday availability on the owner's DB) remains
  NOT VERIFIED until the owner runs START.bat.
