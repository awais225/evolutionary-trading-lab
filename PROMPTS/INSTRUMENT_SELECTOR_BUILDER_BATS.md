# Task prompt — HERO1: Global Instrument Selector, Visual Strategy Builder, BAT Launcher Repairs and Workspace Dashboard
Archived verbatim BEFORE implementation (2026-11-06), per the documentation protocol.

# HERO1: Global Instrument Selector, Visual Strategy Builder, BAT Launcher Repairs and Workspace Dashboard

## Overall objective
Improve the existing Hero1 application in four areas:
1. Add a consistent instrument selector across all dashboard tabs.
2. Build a powerful but simple graphical strategy/condition builder inside Manual Nodes.
3. Fix PREREQUISITES.bat and START.bat, including their logging, startup and window persistence problems.
4. Run the actual updated Hero1 dashboard inside the LMS workspace and expose it through the supported preview mechanism.

This is an incremental improvement to the existing Hero1 project.
Do not redesign unrelated parts of the dashboard, change the existing theme, or modify legacy-archive.
Work on the existing GitHub repository: awais225/evolutionary-trading-lab
Use the existing `main` branch.

# PART A: GLOBAL INSTRUMENT SELECTOR
## 1. Instrument selection on every tab
At the top-right of the dashboard, the current application status indicators already show items such as MT5, Ollama, Backend. Keep these status indicators. Add an instrument dropdown in this same top-right area. The instrument selector must also be visible on every main dashboard tab, so the user always knows which instrument is currently selected. The eight tabs: Manual Nodes, WWW Research, Interactive WWW, DeepTesting, Fine Tuning, Live Testing, LOG, SETTINGS.
### Default instrument
The default selected instrument must always be: XAUUSD
### Initial supported instruments
Populate the selector using instruments supported by the existing Hero1 configuration and available MT5 symbol metadata. Examples may include XAUUSD, EURUSD, GBPUSD, USDJPY, BTCUSD, ETHUSD. Do not assume every broker supports every symbol. Show unsupported or unavailable symbols appropriately rather than pretending they are available.
### Global synchronization
The instrument selection must be shared global application state: if I select BTCUSD in Manual Nodes, the selector on all other tabs must also show BTCUSD; if I switch to EURUSD in DeepTesting, the global selected instrument changes; navigating between tabs must not reset the selected instrument; refresh behavior must follow a clearly defined persistence policy; default to XAUUSD when there is no previously saved selection. The selected instrument must be passed explicitly to all relevant frontend requests and backend operations.
### Instrument isolation
A strategy or node created for XAUUSD must not accidentally be applied to BTCUSD. All relevant data, nodes, strategy versions, validation and future testing operations must remain instrument-specific. Display the currently selected instrument clearly wherever an operation is being performed. Before executing an operation, verify that its instrument matches the selected instrument. Do not silently mix data between instruments.

# PART B: MANUAL NODES VISUAL STRATEGY BUILDER
## 2. Objective
The current Manual Nodes experience should support two ways of creating strategies: (1) Graphical/visual strategy builder. (2) Existing JSON/code-based editor. The graphical builder should be the primary and easiest method; keep the existing JSON editor available for advanced users. Both methods must produce the same underlying Hero1 strategy specification. There must be one authoritative strategy representation, not separate incompatible visual and code-based implementations.
## 3. Indicator selection panel
Add an indicator selection panel with searchable indicator categories. Initial indicator library:
### Moving averages: SMA, EMA, WMA, SMMA (if supported)
### Momentum: RSI, ROC, MACD, Stochastic, CCI (if supported)
### Volatility: ATR, Bollinger Bands, Standard Deviation
### Volume and price-based: VWAP, Volume, OBV (if supported)
### Trend: ADX, Parabolic SAR (if supported)
Use the existing indicator identifier registry and schema. Do not invent indicator identifiers that are not supported by the backend. The interface should allow searching for an indicator by name. When I select an indicator, it should be added as a configurable condition row.
## 4. Visual condition builder
Each condition should have configurable fields: left-hand indicator or price variable; indicator parameters; timeframe; comparison operator; right-hand indicator, price variable or numeric value; optional candle offset/index, following Hero1's existing candle indexing rules. Examples: EMA(20) >= EMA(50); RSI(14) <= 30; Close > VWAP; ATR(7) > 2.5; MACD crosses above Signal.
### Comparison operators — use the existing validated expression operators: Greater than >, Greater than or equal >=, Less than <, Less than or equal <=, Equal ==, Not equal !=, Crosses above, Crosses below. Do not implement operators that are unsupported by the backend schema without updating and testing the authoritative schema.
### Parameter configuration — when selecting an indicator, show its configurable parameters (SMA period/applied price/timeframe; EMA period/applied price/timeframe; RSI period/timeframe; ATR period/timeframe; Bollinger Bands period/stddev multiplier/applied price/timeframe; VWAP supported configuration and timeframe/session parameters; MACD fast/slow/signal/timeframe). Use sensible defaults but allow editing. Validate parameters before saving. Do not allow invalid or unsupported configurations.
## 5. Indicator values and live preview
When an indicator or condition is selected, provide a value preview (e.g. `EMA(20): 3985.42`). Display the instrument and timeframe used to calculate each value. The values must come from actual available market data and real indicator calculations. Do not generate fake values, hardcode sample values, or display random numbers. If the current market-data or indicator-calculation engine is not yet implemented or available: clearly display DATA UNAVAILABLE or INDICATOR ENGINE NOT AVAILABLE; keep the graphical condition builder functional; do not pretend the indicator is being calculated; document the missing dependency and the required future implementation. Do not silently introduce a new market-data architecture or alter the existing strategy specification to simulate indicator calculations. A read-only indicator preview must never place trades or modify broker state.
## 6. Condition groups and AND/OR logic
Combine multiple conditions easily. Support AND groups and OR groups; add condition; remove condition; duplicate condition; add condition group; delete group; select AND/OR between conditions; nest groups where supported by the backend schema. Follow the existing maximum expression depth and node limits. Show the condition hierarchy clearly. Avoid overwhelming the user with unnecessary nested configuration.
## 7. Entry, exit and risk sections
Keep the strategy builder organized into clear sections: 1. Entry Conditions; 2. Exit Conditions; 3. Risk Management; 4. Strategy Information. Do not automatically invent exit conditions. Entry and exit logic must remain explicit. Risk parameters must preserve Hero1's existing explicit unit model: ABSOLUTE, POINTS, TICKS, PIPS, ATR_MULTIPLE. If broker metadata is missing, preserve the existing NEEDS_METADATA validation behavior. Do not assume pip or tick values.
## 8. Node naming and numbering
At the bottom of the Manual Nodes builder, add a dedicated Node Name field.
### Automatic naming
By default, automatically assign names in this sequence: C1, C2, C3, ... The next available number must be determined from the existing node registry for the relevant instrument. Do not reset numbering simply because the page is refreshed. Do not reuse an existing name accidentally.
### Manual naming
Allow the user to override the automatically generated name (e.g. XAU Breakout, BTC Momentum, EMA Crossover). If the user leaves the field unchanged, use the automatic C-number. If a custom name is entered, preserve it. Do not create duplicate names where uniqueness is required by the existing registry.
### Important
Node naming must be separate from node IDs and strategy hashes. The display name must not accidentally change the semantic strategy definition or cause unnecessary version changes.
## 9. Graphical builder and JSON editor synchronization
Provide two modes: Visual Builder and JSON Editor. The same strategy must be represented in both. Changes made in the Visual Builder must update the underlying strategy specification. Changes made in JSON must be reflected in the Visual Builder when the JSON is valid and supported. If the JSON contains unsupported structures, display a clear message rather than silently deleting information. Keep existing Validate, Compile, Save, Save as New Version and Lineage functionality. The backend remains authoritative. Do not duplicate validation rules in the frontend and assume they are sufficient. All final validation and compilation must use the existing backend APIs.
## 10. User experience
Searchable indicator selection; clear condition rows; dropdowns for operators; numeric inputs for values; indicator-to-indicator comparisons; add/remove buttons; AND/OR group controls; clear validation messages; a compact node summary. Avoid displaying a huge list of every possible indicator configuration simultaneously. Only show parameter controls relevant to the selected indicator. Keep the existing Hero1 theme and visual language.

# PART C: DATA AND LOGGING DIRECTORY CORRECTION
## 11. One canonical DATA directory
I want all application-generated data and logs stored inside one DATA folder located directly inside the Hero1 project root. The project root means the directory containing PREREQUISITES.bat, START.bat, pyproject.toml, apps, packages, services. The required layout: <PROJECT_ROOT>/DATA/{DATABASE,LOGS,BACKUPS,CACHE,MARKET_DATA,RESEARCH,ARTIFACTS}. Configure HERO1_DATA_ROOT=<PROJECT_ROOT>\DATA. For example, if the source project is D:\Hero1\app then the data root must be D:\Hero1\app\DATA. Do not store data in an unrelated drive root, a separate user home directory, a random system temporary folder, a sibling folder outside the project directory, or the same folder as the BAT files without the DATA subdirectory. All application-generated logs must be stored under <PROJECT_ROOT>\DATA\LOGS\. Required log files: prerequisite.log, startup.log, backend.log, frontend.log.
### Existing DATA preservation and migration
Before changing the configured DATA path: inspect the current DATA location; identify existing databases, research files, logs and artifacts; do not delete or overwrite existing data; if existing data is currently stored in D:\Hero1\DATA, perform a safe copy into the new canonical project-root DATA folder if required; verify copied file counts, sizes and database integrity; keep the original DATA untouched until the new location is verified; do not create two competing active databases; document the final authoritative DATA path. Add the DATA directory to .gitignore. Never commit databases, market data, generated research data, logs, credentials or backups to GitHub.

# PART D: REPAIR PREREQUISITES.bat AND START.bat
## 12. Current PREREQUISITES.bat problem
The current prerequisite script reports `DATA root: D:\Hero1\DATA` and `Log file: D:\Hero1\DATA\LOGS\prerequisite.log`. Correct this to use `<PROJECT_ROOT>\DATA\LOGS\prerequisite.log`. Use the canonical DATA path defined in Part C. Verify that every logging operation uses the same path. Do not hardcode unrelated paths throughout the BAT file. Use one clearly defined DATA_ROOT variable and derive all other paths from it.
## 13. Current START.bat problem
The current START.bat is broken. When double-clicked a CMD window opens, it immediately closes, it appears as a flicker, the backend and frontend do not remain running, no usable dashboard is opened. Diagnose the actual cause instead of merely changing the window appearance. Inspect batch syntax, working directory, %~dp0 handling, quoting, environment variable expansion, virtual environment interpreter path, Node/npm paths, backend command, frontend command, process creation and lifetime, port conflicts, exit codes, startup dependencies, health-check implementation.
## 14. PREREQUISITES.bat requirements
It must work by double-clicking; work when invoked from CMD or PowerShell; resolve project root using %~dp0; detect Python 3.11+; detect Node.js 20 LTS and npm; create or reuse .venv; install all required Python packages; install frontend dependencies using npm ci where appropriate; verify backend imports; build the frontend; initialize/check the database without deleting existing data; configure the canonical DATA root; create the required DATA subdirectories; log everything to <PROJECT_ROOT>\DATA\LOGS\prerequisite.log; check every important command's exit code; display clear success/failure messages; pause on failure and at completion so the window does not disappear unexpectedly.
## 15. START.bat requirements
This is the everyday launcher. It must: (1) resolve project root using %~dp0; (2) set HERO1_DATA_ROOT to the canonical project-root DATA directory; (3) verify prerequisite installation; (4) verify database availability; (5) check port availability; (6) detect existing Hero1 processes and avoid duplicate instances; (7) launch backend in a separate visible persistent CMD window; (8) launch frontend in a separate visible persistent CMD window; (9) keep both service windows open using appropriate persistent CMD behavior; (10) wait for the backend's real /health endpoint; (11) wait for actual frontend readiness; (12) open the browser only after both are ready; (13) write diagnostic logs to the canonical DATA/LOGS directory; (14) show useful errors rather than silently closing. If startup fails: display the failed step, display the relevant log path, keep the launcher window open, do not kill unrelated processes, do not report success falsely. Repeated execution must not start duplicate backend/frontend instances.

# PART E: WORKSPACE DASHBOARD PREVIEW
## 16. Final workspace launch
After implementation, tests and GitHub publication, start the actual updated Hero1 application inside the current LMS workspace. I want to inspect the real running dashboard immediately. Requirements: detect the actual workspace environment; install only the dependencies required by Hero1; use a separate workspace DATA directory outside the Git source directory; initialize a separate workspace database if necessary; start the actual Hero1 backend; verify the backend health endpoint; start the actual Hero1 frontend; ensure the frontend communicates with the workspace backend; expose the dashboard through the LMS supported preview or forwarded-port mechanism; verify that the actual application loads; keep the services alive using the platform's supported persistent mechanism. Do not use a fake dashboard, static HTML page or mock application. Do not claim that Linux workspace testing proves Windows BAT compatibility. Do not claim GPU or MT5 functionality unless actually available and tested.
### Workspace UI inspection
Once running, inspect all eight tabs: Manual Nodes, WWW Research, Interactive WWW, DeepTesting, Fine Tuning, Live Testing, LOG, SETTINGS. Verify: instrument selector appears consistently; default instrument is XAUUSD; changing the instrument updates the shared application state; Manual Nodes graphical builder loads; existing JSON editor still works; existing validation and compilation functionality remains intact; existing navigation and dashboard theme remain intact. Report any incomplete features honestly. Provide the actual workspace preview URL.

# PART F: TESTING AND REGRESSION PROTECTION
## 17. Required tests
### Instrument selector
Default XAUUSD; select BTCUSD; navigate through all tabs; confirm selected instrument remains BTCUSD; switch to EURUSD; confirm global state updates; refresh and test persistence; confirm instrument-specific nodes are isolated.
### Visual builder
Add SMA, EMA, RSI, VWAP, ATR; change indicator parameters; change comparison operators; compare two indicators; compare an indicator with a numeric value; test AND; test OR; add/remove/duplicate conditions; test invalid parameters; test unsupported indicator configurations; test indicator values where actual data is available; confirm unavailable data is clearly reported without fake values.
### Node naming
First new node receives C1; second receives C2; existing node names are not duplicated; refresh does not reset numbering; manual custom naming works; custom naming does not corrupt node IDs or semantic hashes.
### JSON synchronization
Visual Builder generates valid JSON; JSON editor updates Visual Builder; invalid JSON is handled safely; existing backend validation/compile APIs remain authoritative; existing versioning and lineage remain functional.
### BAT files
Test prerequisite detection; dependency installation/reuse; backend imports; frontend production build; database integrity; logging paths; run START.bat; verify backend health; verify frontend readiness; verify browser opens after readiness; verify persistent service windows; verify repeated launch behavior; verify failure handling. If Windows execution is unavailable, explicitly state that Windows BAT tests were not performed.

# PART G: SCOPE, GIT AND FINAL REPORT
## 18. Scope protection
Do not: modify legacy-archive; change unrelated dashboard styling; rewrite the backend architecture; change existing strategy semantics; invent indicator values; introduce fake market data; place live trades; change the database schema unless genuinely necessary and explicitly justified; delete or reset existing DATA; commit secrets or credentials. Keep the implementation modular and compatible with future market-data, indicator, backtesting and MT5 development. If a feature requires functionality that does not yet exist, clearly identify the missing dependency rather than implementing a fake substitute.
## 19. GitHub publication
Inspect the full diff. Run all relevant tests. Commit and push to awais225/evolutionary-trading-lab. Use main. Preserve the existing Hero1 history and all legacy branches/tags. Report: exact files changed; exact tests performed; test results; any unresolved limitations; commit SHA; push confirmation; verified remote main SHA. Do not include credentials in any file, script, log, command history or commit.
## 20. Final report
Provide sections A. Instrument selector; B. Manual Nodes; C. Data and logging; D. Windows launchers; E. GitHub; F. Workspace dashboard (backend health, frontend status, actual preview URL, tabs inspected, features working and incomplete).

Acceptance criteria — the task is complete only when:
1. Every tab displays the current instrument.
2. XAUUSD is the default.
3. Instrument selection is synchronized and instrument data is isolated.
4. Manual Nodes supports graphical indicator conditions and AND/OR logic.
5. JSON and graphical modes remain synchronized.
6. Node names automatically progress C1, C2, C3, with manual override.
7. All application data and logs use <PROJECT_ROOT>\DATA.
8. PREREQUISITES.bat installs and verifies the environment.
9. START.bat actually starts the backend and dashboard without flickering or silently closing.
10. Both services remain running until deliberately stopped.
11. Changes are committed and pushed to the existing repository.
12. The actual updated dashboard is running in the LMS workspace and available for inspection.

Priority: correctness, real functionality, data safety and reliable startup. Do not sacrifice these for a superficial UI demonstration.
