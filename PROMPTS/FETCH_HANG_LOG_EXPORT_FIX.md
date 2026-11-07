<!-- Task prompt archived as received (user message, 2026-11-07). Original prompt begins below. -->

# HERO1 — URGENT PATCH: DATA FETCH HANG, BACKEND INTERNAL SERVER ERROR & COMPLETE LOG EXPORT

## PRIORITY

The newly implemented DATA tab has introduced a serious operational problem.

After opening DATA and clicking FETCH DATA:

1. The FETCH operation became stuck.
2. When navigating away and reopening the application from another tab, the application returned `Internal Server Error`.
3. The LOG tab displayed only 12 events, mostly INFO messages.
4. The logs do not expose the actual processing stage or the backend exception responsible for the failure.
5. The LOG tab also lacks two required features:
   - COPY ALL LOGS
   - SAVE LOGS TO TXT

This task is an urgent diagnostic and stability patch.

Do not redesign the DATA tab or modify unrelated features.

Preserve all existing historical data, node data, CSV imports, working MT5 connectivity, and previous UI improvements.

---

# PART 1 — INVESTIGATE THE STUCK FETCH AND INTERNAL SERVER ERROR

## 1.1 Reproduce and identify the failure

Start by inspecting:

- Current Git branch and working-tree status.
- Latest implementation commits.
- DATA tab fetch implementation.
- Backend fetch endpoint.
- MT5 fetch service.
- Database transaction handling.
- Logging implementation.
- Backend process logs.
- Existing fetch operation state and operation ID.

Investigate the specific operation:

`FETCH-20261107-090358-B7E4F1`

Search all available frontend and backend logs for this operation ID.

Determine whether the request:

- Never reached the backend.
- Reached the backend but stalled.
- Failed during MT5 communication.
- Failed during timestamp or candle validation.
- Failed during conflict processing.
- Failed while accessing the database.
- Failed during a database transaction.
- Completed successfully but failed during response serialization.
- Completed but the frontend failed to refresh.
- Caused an unhandled exception or backend process failure.

Do not assume the cause.

## 1.2 Capture the real backend exception

The generic HTTP 500 response is insufficient.

Capture and log:

- Full exception type.
- Exception message.
- Full server-side stack trace.
- Request endpoint.
- Operation ID.
- Instrument.
- Fetch scope.
- Current timeframe.
- Current processing stage.
- Database path.
- Relevant database transaction state.
- MT5 connection status.
- Elapsed processing time.

Ensure the actual exception is visible in persistent backend logs.

The frontend may display a concise user-friendly error, but the full technical details must be available in the LOG tab.

Never expose credentials, tokens, passwords, or secrets in logs.

## 1.3 Investigate request blocking and concurrency

Check whether the fetch endpoint is blocking the application server while processing MT5 history.

Investigate:

- Synchronous MT5 operations blocking the event loop.
- Long-running requests.
- Database locks.
- SQLite write contention.
- Unclosed transactions.
- Unreleased database connections.
- Deadlocks.
- Excessive concurrent fetch requests.
- Frontend polling requests overlapping with the active fetch.
- Multiple DATA tab mounts initiating duplicate requests.
- A previous fetch operation leaving the application in a permanently active state.

If the fetch service is blocking the backend event loop, implement an appropriate background execution mechanism compatible with the existing architecture.

Do not introduce a new queue or infrastructure unnecessarily.

## 1.4 Fetch operation state management

Implement reliable operation states:

- IDLE
- QUEUED
- RUNNING
- COMPLETED
- PARTIAL
- FAILED
- CANCELLED

Requirements:

1. Generate one operation ID per user-initiated fetch.
2. Prevent accidental duplicate requests while an operation is already running.
3. Ensure the operation state survives frontend tab navigation.
4. Reopening DATA must retrieve the existing operation status.
5. A frontend refresh must not restart the same fetch.
6. A failed request must not leave the UI permanently stuck.
7. A failed operation must release database connections, locks, and other resources.
8. The user must be able to distinguish a running operation from a failed operation.

If supported by the existing architecture, allow a user to cancel a running fetch safely.

Cancellation must not leave a partially committed or corrupted database transaction.

## 1.5 Timeout and failure handling

Implement appropriate timeouts for MT5 requests and backend processing.

A timeout must:

- Be logged with the operation ID.
- Identify the stage that timed out.
- Release resources safely.
- Update the operation state.
- Return a meaningful response to the frontend.

Do not simply increase the timeout indefinitely to hide a stuck process.

## 1.6 Protect the other application tabs

The DATA fetch operation must not make the rest of Hero1 unavailable.

After a fetch begins:

- The user must be able to navigate to another tab.
- Other dashboard APIs must continue responding.
- The LOG tab must remain accessible.
- Node information must remain accessible.
- The frontend must not wait for a long-running HTTP request before rendering unrelated tabs.

Investigate why reopening from another tab produced an Internal Server Error.

Determine whether the problem is limited to one endpoint or whether the entire backend process became unhealthy.

---

# PART 2 — COMPLETE FETCH LIFECYCLE LOGGING

The current logs show that FETCH DATA was clicked, but they do not show what happened afterwards.

Repair this.

Every fetch operation must produce a traceable sequence of persistent events using the same operation ID.

Required stages:

1. FETCH_CLICKED
2. FRONTEND_VALIDATION
3. REQUEST_SUBMITTED
4. BACKEND_REQUEST_RECEIVED
5. OPERATION_CREATED
6. DATABASE_PATH_RESOLVED
7. MT5_CONNECTION_CHECK
8. SYMBOL_METADATA_CHECK
9. STORED_BASELINE_READ
10. TIMEFRAME_FETCH_STARTED
11. MT5_HISTORY_REQUEST
12. MT5_HISTORY_RESPONSE
13. TIMESTAMP_VALIDATION
14. CANDLE_VALIDATION
15. DUPLICATE_CHECK
16. CONFLICT_CHECK
17. DATABASE_TRANSACTION_STARTED
18. DATABASE_INSERT
19. DATABASE_COMMIT
20. POST_COMMIT_VERIFICATION
21. TIMEFRAME_COMPLETED
22. DASHBOARD_REFRESH
23. OPERATION_COMPLETED

These are logical stages. Use the actual implementation's processing boundaries rather than creating misleading events.

Every event should contain:

- Timestamp with timezone.
- Severity.
- Component.
- Event name.
- Operation ID.
- Instrument.
- Timeframe, where applicable.
- Processing stage.
- Elapsed time, where applicable.
- Message.
- Relevant structured details.

If any stage fails, record:

- Exact failed stage.
- Exception type.
- Exception message.
- Full backend stack trace.
- Whether a database transaction was rolled back.
- Whether any valid records were committed before the failure.

Use DEBUG for detailed diagnostic events, INFO for normal progress, WARNING for recoverable problems, and ERROR for actual failures.

Do not generate excessive duplicate logs from frontend rendering or polling.

---

# PART 3 — REPAIR THE LOG TAB

The LOG tab currently shows only a small number of loaded events and does not provide adequate diagnostics.

Make it a reliable interface for reading persistent logs.

## 3.1 Log loading

Inspect whether the current limit of 12 events is:

- A frontend display limit.
- A backend pagination limit.
- A database query limit.
- A hardcoded API limit.

Implement proper pagination or incremental loading.

The interface should:

- Show the newest events first.
- Display the total number of matching events.
- Support loading older events.
- Support searching by operation ID.
- Support filtering by severity and component.
- Support filtering by instrument and timeframe.
- Allow viewing the full details of an event.
- Refresh without losing the user's active filters.

Do not silently truncate full stack traces or event details.

## 3.2 ADD COPY ALL LOGS

Add a clearly visible button:

**COPY ALL LOGS**

Expected behavior:

- Copy all logs matching the current filters.
- Include all available matching events, not merely the 12 currently displayed.
- Preserve chronological ordering, preferably oldest first for reading a complete operation.
- Include timestamps, severity, component, operation ID, event name, and message.
- Include full expandable details and stack traces.
- Use readable plain text formatting.
- Handle large log volumes without silently truncating content.

If the browser clipboard API fails, display a clear error and offer an alternative.

Do not report success unless the copy operation actually succeeds.

## 3.3 ADD SAVE LOGS TO TXT

Add a second button:

**SAVE LOGS TO TXT**

Expected behavior:

- Export all logs matching the active filters.
- Do not export only the visible page.
- Retrieve the complete matching log dataset from the backend when necessary.
- Include complete event details and stack traces.
- Use UTF-8 encoding.
- Generate a downloadable `.txt` file.

Suggested filename:

`HERO1_LOGS_YYYY-MM-DD_HH-MM-SS.txt`

The exported file should include:

- Hero1 application name.
- Export timestamp.
- Active filters.
- Total exported events.
- Event details in chronological order.
- Complete operation traces.
- Full exception details.

If no filters are active, export all available logs.

If a particular fetch operation is selected, allow exporting its complete operation trace separately.

Do not silently exclude older records due to pagination.

## 3.4 LOG TAB UI

Place both buttons prominently near the top of the LOG tab.

Suggested arrangement:

`[COPY ALL LOGS] [SAVE LOGS TO TXT] [REFRESH]`

Use existing Hero1 dark/amber styling.

Keep existing search and filters.

Do not introduce generic unstyled buttons.

---

# PART 4 — DATA TAB STABILITY

Inspect the recently created DATA tab for lifecycle issues.

Ensure:

- Opening DATA does not accidentally start a fetch.
- Reopening DATA does not create duplicate requests.
- Leaving DATA does not cancel an active operation unexpectedly.
- The fetch status is retrieved from the backend.
- The active operation ID remains visible.
- Progress updates do not trigger repeated API requests.
- Failed operations display the actual failure stage.
- Historical data availability refreshes only when appropriate.
- The node dashboard remains independent of the historical fetch process.

The historical-data and node-dashboard sections must remain usable even when MT5 fetching fails.

---

# PART 5 — DATABASE AND HISTORICAL DATA SAFETY

This patch must not damage existing market data.

Do not:

- Delete or recreate the canonical market-data database.
- Force-overwrite conflicting historical candles.
- Reset the historical baseline.
- Delete CSV source files.
- Delete generated nodes.
- Delete lineage records.
- Change semantic fingerprints.
- Modify node generation logic.

After the patch:

1. Verify database integrity.
2. Verify candle counts before and after.
3. Verify the latest stored candle per timeframe.
4. Verify no duplicate records were introduced.
5. Verify a failed fetch leaves the database consistent.
6. Verify a backend restart does not lose existing data.

---

# PART 6 — TESTING

Perform these focused tests.

### Fetch lifecycle

- [ ] FETCH DATA starts one operation.
- [ ] An operation ID is generated.
- [ ] The operation progresses through logged stages.
- [ ] The backend does not block unrelated application APIs.
- [ ] Navigating to another tab does not interrupt the operation.
- [ ] Reopening DATA retrieves the same operation.
- [ ] Duplicate fetch requests are prevented.
- [ ] A timeout produces a useful failure.
- [ ] An exception produces a full backend stack trace.
- [ ] A failed fetch releases resources.
- [ ] The frontend never remains permanently stuck.

### LOG tab

- [ ] More than 12 events can be retrieved.
- [ ] Pagination or loading older events works.
- [ ] Operation ID filtering works.
- [ ] Full error details are accessible.
- [ ] COPY ALL LOGS exports all filtered events.
- [ ] SAVE LOGS TO TXT downloads a complete UTF-8 text file.
- [ ] Exports are not limited to the visible page.
- [ ] Exported logs include complete stack traces.
- [ ] Exporting logs does not delete or alter stored logs.

### Application stability

- [ ] DATA tab works.
- [ ] Other tabs remain responsive during fetch.
- [ ] LOG tab works while fetch is running.
- [ ] Existing MT5 connection remains functional.
- [ ] Existing historical data remains intact.
- [ ] Existing node data and lineage remain intact.
- [ ] Backend restart works correctly.

Run relevant backend tests, frontend tests, and production build.

Do not claim Windows/MT5 integration tests passed unless they were actually executed in the Windows environment.

---

# PART 7 — DOCUMENTATION AND GIT

Before changes, record the starting commit SHA and working-tree status.

Review the existing troubleshooting documentation.

Update:

- `TROUBLESHOOTING.txt`
- `CHATGPT.txt`
- `CHANGELOG.md`
- `docs/ISSUES_AND_FIXES.md`
- `docs/ARCHITECTURE.md`
- `docs/IMPLEMENTATION_PROGRESS.md`
- `docs/PROMPTS/` with this exact prompt.

Add the confirmed cause of the stuck fetch and Internal Server Error to the troubleshooting knowledge base.

Separate confirmed causes from possible causes.

Commit and push to the existing GitHub repository and `main` branch.

Verify the remote SHA after pushing.

Do not modify `legacy-archive`.

Do not create a release tag until the patch and required tests are complete.

---

# FINAL REPORT

Provide:

1. Starting commit SHA.
2. Final commit SHA.
3. Verified remote main SHA.
4. Confirmed root cause of the stuck fetch.
5. Confirmed root cause of the Internal Server Error.
6. Actual backend exception and failing stack location.
7. Fetch operation lifecycle and recovery behavior.
8. Logging changes.
9. COPY ALL LOGS verification.
10. SAVE LOGS TO TXT verification.
11. Number of events exported in the test.
12. Database integrity results.
13. Historical candle counts before and after.
14. Tests executed and actual results.
15. Files changed.
16. Remaining limitations.

**Do not mark this task complete simply because the buttons have been added or the frontend no longer displays an error. Verify the actual backend behavior, persistent operation trace, complete log export, and database integrity.**

## OUTCOME (2026-11-07, HERO-V1.5.0 repair line, ISSUE-052)

Starting SHA 6c4c7b0e57f277e304328dbfc1b991d9864f2098. The owner's
operation FETCH-20261107-090358-B7E4F1 was generated on their Windows
machine and does not exist in the LMS event store (searched); the old
code captured no Windows exception - stated honestly in the report.
CONFIRMED defects fixed: single blocking sync fetch (no operation state/
resume/duplicates possible), no MT5 timeout, failure paths without stack
traces in the persistent log, LOG oldest-first capped loading with no
total/pagination/export. Implemented: background operations
(start/status/latest, duplicate-start returns the running op, DATA tab
polls 700ms and resumes), per-timeframe MT5 timeout (180s default,
stage MT5_HISTORY_REQUEST), canonical lifecycle stages with full stack
traces (incl. unhandled-500 capture), events offset/order/total, LOG
Load-older + COPY ALL LOGS + SAVE LOGS TO TXT (complete filtered export).
Live Linux: ALL operation RUNNING -> FAILED (MT5_UNAVAILABLE) with a
6-event trace; events total=30 paged; unrelated APIs responsive;
canonical DB unchanged (419,013 candles, integrity ok, 0 duplicates).
Tests: backend 204 passed / 1 skipped (205); frontend 51/51; build
clean. Commit aa02a11929d73f10554831d66943bc923bbcf2ba (remote main
verified; HERO-V1.5.0 not moved). Windows NOT VERIFIED: after git pull,
any recurrence now appears in LOG as an ERROR event with stack trace -
return that trace (or use SAVE LOGS TO TXT with the operation id filter).
