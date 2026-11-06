# Task prompt — HERO1: CRITICAL RECOVERY AND IMPROVEMENT TASK
Archived verbatim BEFORE implementation (2026-11-06), per the documentation protocol.

Restore the Proven Legacy Launcher, Enforce Theme Consistency, Add Starred Nodes and Preserve the Trading Research Pipeline

IMPORTANT: READ THIS BEFORE MAKING ANY CHANGES

We are repeatedly encountering problems that were already solved in the previous Evolutionary Trading Lab.

I explicitly asked that the previous dashboard's implementation, branch logic and troubleshooting history be inspected before creating Hero1's launchers. This was not followed properly, and START.bat is still failing.

I do not want another experimental launcher implementation.

I want you to inspect the actual working legacy implementation, identify why it worked, compare it with Hero1, and apply the proven architecture correctly.

Do not start by rewriting START.bat.

First investigate. Then identify the root cause. Then implement and verify the fix.

Repository: awais225/evolutionary-trading-lab
Current branch: main
Legacy reference branch: v10.4-throughput
Also inspect: v10.3-speed-diagnostics, v10.2-dashboard-manual-testing, legacy-archive

Do not modify legacy branches or their historical contents.

PART 1: RECOVER THE PREVIOUS DASHBOARD'S WORKING LAUNCHER

1. Read the actual legacy implementation — Read the complete files from the legacy branch, not just their documentation. Start with: start.bat, repair.bat, Prerequisite.bat / prerequisite.bat, scripts/run_backend.bat, Backend entrypoint, Backend health-check utilities, Port-check utilities, Frontend build verification utilities, Database verification utilities, Startup logging and monitoring implementation. Inspect all relevant launcher dependencies.
Reference: https://github.com/awais225/evolutionary-trading-lab/blob/v10.4-throughput/start.bat

The previous launcher already solved the following problems: 1. Correct project-root detection. 2. Correct Python interpreter resolution. 3. Backend import verification. 4. Database verification. 5. Frontend production build verification. 6. Existing server detection. 7. Port ownership checks. 8. Safe process handling. 9. Persistent backend terminal window. 10. Real HTTP health polling. 11. Verification of the actual server instance. 12. Browser launch after readiness. 13. Foreground monitoring loop. 14. Visible failure diagnostics. 15. No silent termination.
Recover the actual implementation and associated utilities. Do not simplify away mechanisms that were responsible for reliability.

2. Recover the exact previous failure and fix — The previous launcher had a documented batch parsing failure: `are was unexpected at this time.` The problem was caused by fragile nested batch logic. The solution was to simplify the execution path and invoke the correct Python executable directly rather than adding more nested batch layers. The previous backend startup used the equivalent of: `".venv\Scripts\python.exe" -m uvicorn app.main:app` from the correct backend working directory. Hero1 has a different module path, so determine the exact correct command from the current Hero1 codebase. Do not blindly reuse the old module path or old port.

3. Compare old and new architectures — Prepare an internal comparison: Component | Legacy dashboard | Current Hero1 — Project root, Python environment, Backend entrypoint, Frontend serving, API port, Frontend port, Health check, Database verification, Process management, Logging.
Identify exactly which old mechanisms can be reused and which require adaptation. Do not assume Hero1 should use the legacy port or frontend serving architecture.

4. Diagnose current START.bat before changing it — Reproduce the flickering failure. Capture: Actual CMD exit code. Exact command where execution stops. Standard output and errors. Whether the script reaches project-root detection. Whether it reaches Python interpreter detection. Whether the backend import succeeds. Whether the database check succeeds. Whether the backend process is created. Whether the backend process exits immediately. Whether the frontend process is created. Whether a port conflict exists. Whether the browser is ever reached.
Run the launcher through an explicitly persistent diagnostic CMD invocation so that the real error remains visible. Do not use `start` to launch the parent launcher itself in a way that hides its output. Do not use `exit` or error handling that silently closes the only diagnostic window.
Critical requirement — Do not add another BAT wrapper, PowerShell wrapper, EXE or unnecessary launcher. Use the proven legacy design and the minimum supporting utilities.

PART 2: HERO1 STARTUP ARCHITECTURE

5. Adapt the proven startup pipeline — Use the legacy launcher's architecture: 1. Resolve project root. 2. Resolve the exact Python virtual environment. 3. Verify backend imports. 4. Verify database integrity. 5. Verify frontend build/dependencies. 6. Check port ownership. 7. Detect an existing Hero1 instance. 8. Launch backend in a visible persistent CMD window. 9. Poll actual backend health. 10. Verify the responding server is the intended Hero1 instance. 11. Start frontend using the actual Hero1 architecture. 12. Verify frontend readiness and backend connectivity. 13. Open browser. 14. Maintain a foreground monitor loop. 15. Display visible diagnostics on failure.
Use the current Hero1 API entrypoint and frontend configuration. Current Hero1 backend startup has previously been documented as: `python -m uvicorn services.api.app.main:app --host 127.0.0.1 --port 8788` — Verify this against the actual repository before using it. Likewise, inspect the actual frontend Vite configuration and API proxy before selecting its port. Do not assume that a successful frontend HTTP response proves the API connection works.

6. Process management — The backend and frontend must remain running independently of the launcher. Use persistent visible CMD windows where appropriate. Follow the proven `cmd.exe /k` approach from the legacy implementation. The parent launcher should remain open and monitor the application. Repeated execution must detect and safely reuse an existing valid Hero1 instance. Never kill unrelated Python or Node processes. Do not use fragile nested quoting or complicated delayed-expansion expressions without testing them. If a child process fails, its error must remain visible in its own terminal.

7. Readiness and instance verification — Use actual HTTP requests. Do not rely on arbitrary sleep delays. Implement or reuse dedicated, testable utilities for: Port ownership. Backend readiness. Frontend readiness. Server identity verification. Safe process shutdown if required. If a service is already running, verify that it belongs to this Hero1 project and serves the current build before reusing it. A random HTTP 200 response from an unrelated service is not sufficient. The browser must only open after successful backend and frontend verification.

8. Build verification — Use the legacy frontend build identity verification concept where compatible. The launcher should know whether it is serving the frontend built from the current source. Do not silently serve an outdated dashboard. However: START.bat must not reinstall all dependencies every time. START.bat must not run a full prerequisite installation on every launch. Use PREREQUISITES.bat for installation and dependency repair. Only perform the minimum required startup checks during normal launches. If the production build is stale or missing, provide a clear diagnostic and an explicit repair path rather than silently starting an outdated build.

PART 3: UI THEME CONSISTENCY

9. Current visual regression
The initial Hero1 dashboard had proper themed buttons and graphical controls. After adding new features, especially: JSON Editor controls. Visual Builder controls. New condition buttons. Some newly introduced buttons appear in generic grey and white styling. This is unacceptable.
New controls must look like they belong to the existing Hero1 application.

10. Inspect the actual existing design system — Before modifying UI components: 1. Inspect the current theme configuration. 2. Inspect existing button components. 3. Inspect shared input and dropdown components. 4. Inspect existing icon usage. 5. Inspect color tokens. 6. Inspect hover, active, disabled and selected states. 7. Inspect existing graphical cards and panel styles. 8. Inspect the original Manual Nodes interface. Identify the existing reusable components and design tokens. Do not create a separate visual system for the Visual Builder or JSON Editor.

11. Theme consistency rules — Every new component must reuse the existing Hero1 design system. This includes: Primary buttons. Secondary buttons. Icon buttons. JSON Editor controls. Visual Builder controls. Dropdowns. Condition operators. Indicator selectors. Search inputs. Star/favorite buttons. AND/OR controls. Add/remove condition controls. Selected tabs. Filters. Tooltips. Modal dialogs. Empty states. Validation messages. No generic unstyled HTML buttons. No default browser grey/white button appearance. No default browser select appearance unless it has been properly integrated into the theme. Do not introduce arbitrary new colors. Use the existing design tokens and reusable components.

Mandatory visual regression test — Compare the original controls with the newly added controls. New controls must match: Border radius. Typography. Padding. Height. Icon size. Background. Border treatment. Hover state. Focus state. Disabled state. Active state. Check the entire Manual Nodes tab, not only the buttons added most recently. Do not modify unrelated dashboard theme or layout.

PART 4: STARRED / FAVORITE NODES

12. Add star functionality to the custom node table
In the Manual Nodes custom node table, add a star/favorite control for every node. For example: C1 ☆ / C2 ★ / C3 ☆ / C4 ★
Clicking the star should toggle its state immediately. Use the existing Hero1 theme and icon system. Do not use an emoji as the final implementation if the project already has a proper icon system.

13. Persistence — Starred status must persist. It must survive: Page refresh. Tab navigation. Application restart. Backend restart. It must be associated with the actual node identity, not merely the displayed row position or node name. Starred status is user research metadata.
It must NOT: Change strategy conditions. Change the semantic strategy hash. Create a new strategy version. Affect validation results. Modify the strategy's entry or exit rules.
Prefer the existing node metadata or user-preference storage architecture. If a database change is genuinely required, keep it minimal, migration-safe and documented. Do not duplicate strategy records just to store favorites.

14. Starred-node filters across tabs
Add a consistent node filter in relevant research/testing tabs. At minimum: Manual Nodes. DeepTesting. Fine Tuning. Live Testing. Other tabs that select, rank, validate or test nodes.
Provide a simple filter with options: All Nodes. Starred Only. Default should remain All Nodes.
When Starred Only is selected: Only starred nodes should be available for selection. Bulk selection must apply only to the filtered set. Select All must mean all currently filtered nodes, not all nodes in the database. Display the number of matching nodes. Do not accidentally include unstarred nodes in batch testing.
Example: `Showing 24 starred nodes out of 10,000 total nodes`
Ensure the backend enforces the selection scope where applicable. A frontend-only filter must not be treated as a security or correctness boundary for batch operations. The filter state should be clear and consistent across tabs.

PART 5: PRESERVE THE FUTURE TRADING RESEARCH PIPELINE

15. Critical architectural requirement
Hero1 is intended to become a complete multi-instrument trading research and testing platform. The previous dashboard already had extensive work on: Strategy/node generation. Screening. Backtesting. Validation. Qualification. Evolution and lineage. Research logging. Paper testing. MT5 integration. Instrument-specific data. Process management and monitoring.
I do not want Hero1 to be developed in a direction that makes these later stages difficult or requires another architectural rewrite.
Read the legacy pipeline and compare it with Hero1's current architecture. Do not assume that copying the old implementation wholesale is correct. Preserve the proven research concepts while respecting Hero1's newer strategy specification, validation and compiler architecture.

16. Required forward-compatible pipeline
Map the existing legacy pipeline into the Hero1 architecture. The intended conceptual direction is:
1. Strategy definition. 2. Strategy validation. 3. Strategy compilation into a deterministic declarative representation. 4. Instrument and timeframe resolution. 5. Market-data availability and integrity verification. 6. Indicator/feature calculation. 7. Initial screening. 8. Historical backtesting. 9. Robustness and out-of-sample validation. 10. Qualification. 11. Paper testing. 12. Controlled MT5 execution, with risk and safety gates.
Verify the actual ordering and responsibilities from the legacy implementation before finalizing the mapping. Do not implement all future stages in this task. The requirement is to ensure that current changes do not break or obstruct this direction.

Preserve these boundaries
- Strategy specification remains the authoritative source of strategy logic.
- Indicator identifiers and parameters remain declarative.
- Indicator calculations belong in the indicator/feature engine.
- Market data must be instrument-specific and integrity-checked.
- Backtesting must consume validated strategy specifications and the correct instrument/timeframe dataset.
- Validation and qualification must remain separate concepts.
- Paper execution must not be confused with real MT5 execution.
- MT5 execution must have independent risk and safety controls.
- Node lineage must remain intact.
- Starred status must remain independent of strategy semantics.
- Research results must remain reproducible.

Multi-instrument support — Every relevant strategy, dataset, test result and execution context must explicitly identify its instrument. Never mix XAUUSD, EURUSD, BTCUSD or other instrument data. Do not allow a strategy created for one instrument to be silently tested or executed against another. The existing global instrument selector must be respected by all relevant operations.

17. Architecture audit deliverable
Before completing the implementation, document: What legacy pipeline components were inspected. Which existing concepts are already implemented in Hero1. Which are only placeholders. Which are future phases. Whether the current strategy specification and compiler support the intended pipeline. Whether any new changes introduce architectural conflicts. Do not claim future MT5/backtesting capabilities are implemented merely because the architecture is prepared for them.

PART 6: TESTING

18. Launcher testing
Test the actual START.bat on Windows if a Windows environment is available. At minimum: 1. Double-click behavior. 2. CMD invocation behavior. 3. Correct root detection. 4. Correct Python interpreter. 5. Backend import. 6. Database verification. 7. Frontend build verification. 8. Port conflict detection. 9. Existing-instance detection. 10. Backend process creation. 11. Real health polling. 12. Server identity verification. 13. Frontend readiness. 14. Browser launch. 15. Persistent terminal windows. 16. Monitoring loop. 17. Visible failure diagnostics. 18. Repeated launch behavior.
If Windows is unavailable, state this clearly and do not claim BAT compatibility has been proven. Use the LMS workspace to run the actual Linux-compatible backend and frontend separately, but do not treat that as a Windows BAT test.

19. UI testing
Verify: All new buttons match the Hero1 theme. JSON Editor and Visual Builder controls are visually consistent. Indicator condition rows work. Star buttons toggle. Starred status persists after refresh. Starred Only filtering works across relevant tabs. Select All respects the active filter. No unstarred nodes enter a starred-only batch operation. Existing Manual Nodes validation and compilation still work. Existing tabs still load.

20. Regression testing
Run the existing backend and frontend test suites. Add focused tests for new favorite metadata and filtering. Do not modify or delete historical research data. Do not alter legacy-archive.

PART 7: GITHUB AND WORKSPACE PREVIEW

21. GitHub — Use the existing repository and `main` branch. Preserve all existing commits, tags and legacy branches. Inspect the diff before committing. Do not commit: DATA. Databases. Research artifacts. Log files. Credentials. Temporary runtime files. Commit and push only after the relevant tests pass. Report the exact commit SHA and verified remote main SHA.

22. LMS workspace preview
After implementation and publication: Start the actual Hero1 backend in the workspace. Verify its health endpoint. Start the actual Hero1 frontend. Verify backend/frontend communication. Expose the real dashboard through the supported workspace preview mechanism. Keep services alive. Provide the actual preview URL.
Inspect all eight tabs and verify the visual consistency and starred-node controls. Do not substitute static HTML or mock components.

PART 8: FINAL REPORT

Report:
A. Legacy recovery
- Exact legacy files inspected.
- Exact branch and commits referenced.
- Proven launcher mechanisms reused.
- Differences between legacy and Hero1 startup architecture.
B. START.bat
- Original root cause.
- Exact fix.
- Actual Windows test results.
- Whether persistent monitoring works.
C. Theme
- Existing components reused.
- New controls updated.
- Visual regression results.
D. Starred nodes
- Persistence implementation.
- Starred-only filters.
- Batch selection enforcement.
- Tests.
E. Trading pipeline
- Legacy-to-Hero1 mapping.
- Current supported functionality.
- Future stages and dependencies.
- Any architectural risks.
F. GitHub
- Commit SHA.
- Push status.
- Remote main SHA.
G. Workspace preview
- Backend health.
- Frontend status.
- Actual preview URL.
- Tabs inspected.

FINAL INSTRUCTION

Do not repeat the previous cycle of writing a new START.bat, claiming it is fixed, and leaving me with another flickering window.

The old dashboard already contains a proven solution.

Read it, understand it, reuse its architecture, diagnose Hero1's actual failure, and verify the fix.

Do not sacrifice the original Hero1 theme for quick UI additions.

Do not sacrifice the long-term research and MT5 pipeline for short-term functionality.

I want one coherent platform, not a series of disconnected patches.
