# ISSUE-054 — DATA FETCH AUDIT + MANUAL NODES DELETE + DUPLICATE & EDIT

(Verbatim prompt archived from the task assignment; line breaks normalized.)

Implement this as a focused maintenance milestone in the existing Hero1
project. THREE objectives. Do not begin implementing DeepTesting yet.

1. Investigate and resolve the historical market-data discrepancies
   identified in the latest MT5 fetch.
2. Add safe single-node and multi-node deletion to Manual Nodes.
3. Add Duplicate & Edit functionality to Manual Nodes for creating
   independent strategy variants.

Preserve the existing application architecture, theme, strategy semantics,
database and canonical DATA directory.

PART 1 — INVESTIGATE AND RECTIFY HISTORICAL DATA FETCHING
Operation FETCH-20261107-135458-0525F3, broker ICMarketsSC-Demo, reported
M1 30/30/0/0, M5 4280/4277/0/3, M15 1435/1432/0/3, M30 715/712/0/3,
H1 359/356/0/3, H4 95/95/0/0, D1 18/18/0/0 (received/appended/duplicates/
conflicts). All received candles valid; the four conflicting timeframes each
have three conflicts at the beginning of their requested ranges; stored
values preserved, new candles appended. Do not assume these conflicts are
harmless. Do not overwrite stored candles without establishing the cause.
Investigate the exact conflict records (timestamp alignment, instrument/
timeframe identity, UTC vs broker server time, source/import history,
historical MT5 revisions, OHLC / tick volume / real volume / spread diffs,
wrong-timeframe/timestamp assignment). Classify as genuine differences,
broker revisions, alignment errors, source inconsistencies, or incorrect
existing records; never classify on volume/spread alone. Safe resolution:
preserve stored until cause established; preserve incoming + provenance;
never silently overwrite; never discard valid new candles; no synthetic
candles; canonical UTC; unresolved when source cannot be established;
controlled correction path (backup, audit, verification) only if stored is
proven incorrect. Verify fetch lifecycle reliability (committed counts,
PARTIAL honesty, per-TF isolation, idempotent retry, no duplication, DB
errors in LOG with op ID + full exception, progress throughout). Verify the
canonical DB (E:\evolutionary-trading-lab\DATA\MARKET_DATA\market_data.db):
before/after totals, per-TF counts, earliest/latest, committed/duplicates/
conflicts resolved/remaining, integrity; restart persistence.

PART 2 — MANUAL NODES DELETE: per-row Delete with confirmation dialog
(ID + name + dependency implications), delete only after confirmation,
refresh table/counts. Bulk: per-row checkboxes, Select All for the filtered
set, Clear Selection, Delete Selected (N). Report successful/failed,
prevent duplicate submissions. Relationship integrity: inspect actual FKs;
never silently cascade-delete children; never leave broken references;
no unrelated deletions; preserve audit/history; no market-data deletion.
Descendants: show consequences, explicit confirmation for supported
dependency handling.

PART 3 — DUPLICATE & EDIT: per-row action; read full spec; draft copy in
the existing editor; clearly a new variant of the original; Save as NEW
node with NEW unique ID; original never modified. Edit name, instrument
(if supported), timeframe, direction, conditions, indicator params,
thresholds/operators, existing editable properties only; no new strategy
parameters; no global timeframe selector. New unique ID; new semantic
fingerprint; original recorded as parent/source; duplication event in
lineage; renaming alone never changes identity; existing duplicate/
fingerprint rules apply (no silent duplicate identities). Save Variant /
Cancel semantics.

PART 4 — TESTING AND ACCEPTANCE: reproduce/inspect all previously reported
conflicts, document root cause, verify corrections against source records,
repeat-fetch idempotency, restart persistence, honest unresolved reporting.
Deletion tests (one, several, select-all-filtered, cancel, descendants,
integrity/lineage, unrelated data intact after restart). Duplicate tests
(unedited, timeframe change, direction change, condition change, unique
IDs/fingerprints, lineage, original unchanged, cancel creates nothing,
duplicate-fingerprint handling). Regression: backend tests, frontend tests,
build, DB integrity, startup, existing MT5 connection, existing node
generation. Never claim Windows MT5 verification unless actually run there.

PART 5 — Update CHATGPT.txt, CHANGELOG.md, docs/ISSUES_AND_FIXES.md,
docs/ARCHITECTURE.md if changed, docs/IMPLEMENTATION_PROGRESS.md, PROMPTS/.
Commit + push main. Do not touch legacy-archive. No release tag. Final
report: (1) conflict root cause, (2) resolved vs remaining conflicts,
(3) before/after DB counts, (4) deletion implementation + test evidence,
(5) Duplicate & Edit implementation + test evidence, (6) backend/frontend
results, (7) build result, (8) exact commit SHA + remote confirmation.
Priority: data correctness, then deletion, then Duplicate & Edit.

---

## OUTCOME (ISSUE-054)

Part 1: the 12 conflicts (4 TFs x 3) are EXACTLY the fetch overlap window
(latest-2*step .. latest = 3 candles) at the CSV-baseline seam, proven from
the request construction and the zero-duplicate pattern. Stored seam
candles are all CSV-import provenance (verified per-TF dataset -> source
file); incoming are MT5 ICMarketsSC-Demo. From this Linux sandbox the
Windows operation's stored-vs-incoming values are NOT retrievable (0 rows
for FETCH-20261107-135458-0525F3 in this registry) and root cause is
classified UNRESOLVED (ranked hypotheses: History-Center-vs-terminal source
difference / broker history revision at the seam; a systemic epoch offset
is argued against because duplicates elsewhere are exact and cross-TF
aggregation at the seam is internally consistent). No candle was
overwritten. Delivered: durable fetch_conflicts audit (stored + incoming +
provenances + diffs, UNRESOLVED default), GET /market-data/conflicts,
per-record controlled resolution (KEPT_STORED / ACCEPTED_INCOMING with
backup + audit event + unchanged totals) in API + DATA tab, mocked
seam/idempotency/resolution tests.

Part 2: dependency-checked deletion. GET /nodes/{id}/dependencies;
DELETE /nodes/{id} and POST /nodes/delete (bulk, per-node results).
Descendants require explicit include_descendants; dependent records
(backtest/deeptest/live/approvals) always block and are never
cascade-deleted (schema FKs are CASCADE + FK enforcement ON - hence
explicit guards and explicit row deletion). UI: per-row Delete, Delete
Selected (N), confirmation modal with dependency display.

Part 3: POST /nodes/{id}/duplicate + Duplicate & Edit in the existing spec
workspace (Save Variant / Cancel). New id, parent = source, lineage
derived_from, server-recomputed fingerprint; unedited or rename-only
duplicates return 409 SPEC_DUPLICATE / NAME_DUPLICATE per existing rules.

Tests: backend 220 (0 failures, 1 skipped; 14 new), frontend 57/57
(6 new), production build clean. Live Linux: backend restarted, conflicts
endpoint empty, node create->dependencies->delete roundtrip verified,
canonical DB unchanged (419,013, integrity ok, additive fetch_conflicts
table). Windows genuine MT5 verification NOT VERIFIED; the 12 Windows
conflicts remain UNRESOLVED pending the owner's inspection (DATA tab
conflicts panel or GET /market-data/conflicts on Windows).
