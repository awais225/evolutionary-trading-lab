<!-- Task prompt archived as received (user message, 2026-11-07). Original prompt begins below. -->

# HERO1 — CRITICAL FIX: PERMANENT MARKET DATA STORAGE, GITHUB SYNC & WINDOWS DASHBOARD DATA CONSISTENCY

## PRIMARY OBJECTIVE

The LMS server dashboard currently displays the correct historical candle data, but when I open the application on my Windows PC, every timeframe shows no stored data.

Additionally, the previous implementation has failed to understand a critical requirement: the supplied historical CSV candle/bar files must be saved permanently inside the actual Hero1 project directory, both in the LMS workspace and in the existing GitHub repository.

**Saving files in `/tmp`, a scratch directory, a temporary database, or an isolated LMS runtime is NOT an acceptable implementation.**

This task is to diagnose and permanently fix the entire data flow, from the original CSV files to the LMS dashboard, GitHub repository, and Windows application.

Do not simply make the LMS preview display the data. The actual Windows application must be able to access it.

---

## 1. FIRST: INSPECT THE CURRENT STATE

Before modifying anything:

1. Read `TROUBLESHOOTING.txt` and the relevant implementation documentation.
2. Inspect the current Git branch, remote HEAD, tags, uncommitted changes, and existing commits.
3. Identify the actual Hero1 project root.
4. Identify every market-data database currently present.
5. Identify the exact absolute paths of:
   - The database used by the LMS server dashboard.
   - The database used by the Windows application.
   - Any temporary or scratch databases.
   - The original CSV files.
   - The current market-data import implementation.
6. Determine which database contains the correct historical candles currently visible in the LMS dashboard.

Do not assume two databases are identical merely because they have the same filename.

Produce a clear comparison of the database paths, sizes, candle counts, timeframe coverage, and date ranges.

**Do not delete, overwrite, or replace any database during this inspection.**

## 2. PERMANENT CSV STORAGE — NO TEMPORARY DESTINATIONS

The supplied historical CSV files must be stored inside the actual Hero1 project directory.

Use the following permanent project structure, adapting only if the existing architecture has an established equivalent:

`DATA/MARKET_DATA/IMPORTS/XAUUSD/`

Store all seven original XAUUSD CSV files there, one for each supplied timeframe.

Requirements:

- Preserve the original filenames wherever practical.
- Preserve the original CSV contents.
- Do not modify or silently normalize the source files.
- Do not leave the only copy in `/tmp`, scratch space, or a temporary import directory.
- Do not rely on temporary files surviving an LMS restart.
- The permanent files must exist in the actual LMS project workspace.
- The permanent files must also be committed and pushed to the existing GitHub repository, under the same project-relative path.

The GitHub repository is the existing repository:

`https://github.com/awais225/evolutionary-trading-lab`

Use its existing `main` branch for Hero1.

Do not create another repository.

### Important distinction

There are two separate deliverables:

**A. Permanent source CSV files**

These must exist in the LMS workspace and in the GitHub repository under `DATA/MARKET_DATA/IMPORTS/XAUUSD/`.

**B. Imported operational database**

The CSV data must also be imported into Hero1's canonical market-data database, using the existing database architecture.

The application must read from its canonical database, not from a temporary database created during the import process.

If the repository's existing `.gitignore` excludes the entire `DATA` directory, inspect it and make the narrowest possible exception for the seven approved source CSV files.

Do not accidentally commit unrelated databases, generated datasets, user trading data, secrets, or large temporary files.

If the CSV files are not currently attached or available, stop and request the actual seven files. Do not fabricate, reconstruct, or substitute historical data.

## 3. FIX THE LMS AND WINDOWS DATABASE DISCREPANCY

This is the most important technical investigation.

The LMS dashboard displays historical candles, but the Windows application displays zero candles.

Determine exactly why.

Investigate:

- Whether the LMS backend and Windows backend are using different database files.
- Whether the frontend is connecting to a different backend URL or API instance.
- Whether the Windows application has an incorrect database path.
- Whether the database initialization process creates a new empty database on Windows.
- Whether the application is reading a temporary database in one environment and `DATA/MARKET_DATA/market_data.db` in another.
- Whether the market-data API is returning different results depending on environment.
- Whether the imported records are actually committed to the canonical database.
- Whether the database schema or migration state differs between environments.

Do not solve this by hardcoding the LMS database path into the Windows application.

Do not create a second competing canonical database.

### Required architecture

There must be one clearly defined canonical market-data database per running installation.

For the Windows installation, the canonical database should be located inside the actual project directory:

`[HERO1_PROJECT_ROOT]/DATA/MARKET_DATA/market_data.db`

Resolve this path reliably relative to the application/project root, not relative to whichever directory happens to be the current working directory.

The LMS environment must also use a permanent project-relative data location, not `/tmp`.

The application must clearly report the active database path and the environment it is running in.

If the LMS and Windows environments have separate databases, that is acceptable only when the permanent CSV source files are available in both project installations and each environment imports them into its own canonical database.

The Windows dashboard must not depend on the LMS server's temporary database or on a live connection to the LMS server.

## 4. MAKE THE DASHBOARD REPORT REAL DATA AVAILABILITY

The market-data dashboard must display actual stored data for each timeframe.

For XAUUSD, show:

- M1
- M5
- M15
- M30
- H1
- H4
- D1

For every timeframe, display:

- Stored candle count.
- Earliest stored candle.
- Latest stored candle.
- Data availability status.
- Source of the historical data, where known.
- Whether additional MT5 fetching is required.

Also show an overall XAUUSD summary:

- Total stored candles across timeframes.
- Overall available date range.
- Number of timeframes containing data.
- Active database location.
- Last successful import.
- Last successful MT5 update.

The dashboard must query the database actually used by that running application.

Never display a successful data status based on an in-memory cache, stale frontend state, temporary database, or a different server's database.

If no data is available, report zero accurately.

## 5. HISTORICAL CSV IMPORT MUST BE PERSISTENT AND REPEATABLE

Implement a proper import workflow.

The workflow must:

1. Locate the permanent source CSV files inside the project directory.
2. Validate each file and identify its symbol and timeframe.
3. Validate timestamp units, date ranges, OHLC values, and volume fields.
4. Import records into the canonical market-data database.
5. Preserve existing valid records.
6. Avoid duplicate candles.
7. Report imported, skipped, duplicate, invalid, and conflicting records separately.
8. Support safe re-import without duplicating records.
9. Record import provenance and source filename.

The import must not rely on the temporary location where the files were initially uploaded.

After import, restart the backend and verify that the same data is still present.

## 6. MT5 FETCH DATA MUST CONTINUE FROM THE STORED BASELINE

Preserve the existing MT5 connection, which is now working.

Do not regress the connection or launcher changes.

FETCH DATA must:

- Read the actual latest stored candle for each timeframe.
- Fetch subsequent historical candles from MT5.
- Append new valid records.
- Avoid duplicate candles.
- Preserve all existing historical records.
- Handle overlapping requests safely.
- Exclude incomplete candles.
- Report failures separately for each timeframe.
- Never overwrite the existing historical baseline.

Do not use a hardcoded historical start date.

Do not introduce timestamp unit conversions or timezone adjustments without first validating the actual source timestamps and MT5 API expectations.

The previously observed invalid dates, including years such as 2926 and 1970, must be investigated against the actual stored timestamps and source code. Do not merely hide these dates in the UI.

## 7. WORKSPACE AND GITHUB PERSISTENCE VERIFICATION

After implementing the changes, verify all three locations independently:

**Location A: LMS workspace**

Confirm that all seven original CSV files exist under:

`DATA/MARKET_DATA/IMPORTS/XAUUSD/`

Confirm the canonical database exists under:

`DATA/MARKET_DATA/market_data.db`

**Location B: GitHub**

Confirm that the seven original CSV files are actually present in the remote `main` branch at the same project-relative location.

A local file existing in the LMS workspace does not prove that it has been pushed.

A successful Git push message alone is not sufficient. Verify the actual remote commit and file paths.

**Location C: Windows installation**

Confirm that the Windows application uses its project-relative canonical database and can read the imported candles.

If direct access to my Windows PC is unavailable in the LMS environment, do not claim Windows verification. Provide exact Windows verification steps and the diagnostic output I should return.

## 8. TESTING AND ACCEPTANCE CRITERIA

This task is NOT complete until the following conditions are satisfied:

- [ ] Seven original CSV files exist permanently inside the LMS project directory.
- [ ] Seven original CSV files are committed and pushed to the existing GitHub repository.
- [ ] No required source file exists only in a temporary directory.
- [ ] The CSV files are imported into the canonical market-data database.
- [ ] Database records survive a backend restart.
- [ ] Re-importing the same CSV files does not create duplicate candles.
- [ ] The LMS dashboard reports actual stored candles for each timeframe.
- [ ] The Windows installation points to its correct project-relative database.
- [ ] The Windows dashboard can display the imported historical candles.
- [ ] The dashboard's candle counts and date ranges agree with the actual database.
- [ ] MT5 FETCH DATA appends new candles without damaging the historical baseline.
- [ ] No invalid future or 1970 dates are silently accepted.
- [ ] Existing working MT5 connectivity and launcher functionality remain intact.
- [ ] No unrelated tabs, themes, strategy logic, or features are modified.

If Windows verification cannot be performed, explicitly mark it as NOT VERIFIED. Do not mark the task complete.

## 9. TROUBLESHOOTING KNOWLEDGE BASE

Update `TROUBLESHOOTING.txt` with verified findings.

Include:

- Why the LMS dashboard showed data while Windows showed none.
- The actual database paths used in both environments.
- The confirmed root cause.
- The permanent CSV storage location.
- The correct import procedure.
- How to verify timeframe candle availability.
- How to diagnose an empty database or incorrect backend connection.

Separate confirmed causes from hypotheses.

Do not add unverified explanations as established facts.

## 10. GIT AND VERSION CONTROL

Before changes, record the starting commit SHA and working-tree status.

Make only the changes required for this task.

Preserve all existing user data and uncommitted work.

Commit the implementation and documentation changes to the existing `main` branch.

Push the approved CSV source files and relevant source/documentation changes to the existing GitHub repository.

Verify the remote branch and actual file paths after pushing.

Inspect existing release tags before deciding whether a version update is appropriate. Do not create a release tag for an incomplete or unverified implementation.

Do not modify `legacy-archive`.

## 11. FINAL REPORT

Provide a factual report containing:

1. Confirmed root cause of the LMS-versus-Windows discrepancy.
2. Exact LMS workspace project path.
3. Exact permanent CSV directory.
4. Names and sizes of all seven CSV files.
5. Exact canonical database paths.
6. Candle counts and date ranges per timeframe.
7. Total candle count.
8. Import validation results.
9. Evidence that data survives backend restart.
10. Git commit SHA and verified remote `main` SHA.
11. Confirmation that the seven CSV files exist in GitHub.
12. Windows verification result, clearly distinguishing verified from unverified.
13. Tests executed and their actual results.
14. Files changed.
15. Any remaining limitations.

**FINAL INSTRUCTION: Do not tell me the task is complete merely because the LMS server dashboard displays data. The actual permanent project files, GitHub repository, canonical database, and Windows application must all be addressed independently.**

## OUTCOME (2026-11-07, HERO-V1.5.0 repair line, ISSUE-049)

Root cause CONFIRMED: the LMS preview backend ran with an external
HERO1_DATA_ROOT (/home/user/hero1-workspace-data) outside the project, while
the Windows launcher resolves <project>\DATA project-relatively. DATA/ was
fully git-ignored and the supplied CSVs existed only as LMS uploads, so the
Windows checkout received neither CSVs nor any market-data DB — a different,
empty database file with the same name.

Implemented and verified on Linux:
- Seven original CSVs stored permanently at DATA/MARKET_DATA/IMPORTS/XAUUSD/
  (byte-identical SHA-256; .gitattributes -text), tracked via a narrow
  .gitignore exception, pushed to GitHub main; remote tree verified.
- Importer: project-default --data-root + IMPORTS discovery + SHA-256
  early-skip. API lifespan ensure_baseline(); START_INTERNAL stage 6 import;
  START_DIAGNOSTIC step 5b. Dashboard: active DB path, last import vs last
  MT5 update, mt5-fetch-required per timeframe.
- LMS migrated into /home/user/hero1-trading-platform/DATA: rebuilt from repo
  CSVs, 419,013 candles identical to the previous external store, integrity
  ok, survives backend restart, re-import SKIPPED x7 (0.08s).
- Tests: backend 196 passed / 1 skipped (197 collected); frontend 44/44;
  production build clean; launcher audit PASS.
- Commits: implementation 79e8575f2265e133dc3cd9408bb379d3d98c7b23 (remote
  main verified identical; HERO-V1.5.0 tag NOT moved).
- WINDOWS: NOT VERIFIED. Owner: git pull, START.bat, then START_DIAGNOSTIC.bat
  step 5b must list 7 timeframes with candles; dashboard must show the same
  counts and DB path [project]\DATA\MARKET_DATA\market_data.db.
