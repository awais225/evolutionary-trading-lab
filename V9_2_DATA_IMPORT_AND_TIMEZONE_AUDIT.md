# V9.2 DATA and timezone audit

Initial verified remote/main642cd3b1b24737ff223b6d201da61a573e894165. Local snapshot at V6.6 was preserved (patch/stash; matching untracked V9.1 files backed up), then fast-forwarded without reset/clean. Original LFS database SHA256 c159b0718c43b43f97609881bd3be196dad76274029949a9fb4c4e710f106a12 verified; initial DATA preserved separately.

## Confirmed import failure, traced before edits
`validate_frame` truncates its retained `frame` to20,000 rows; commit uses this truncated preview, not the retained raw bytes. Worse, `MasterStore.append` discards every unseen timestamp older than its current last_ts, so a100,856-row historical CSV overlapping a recent2,698-bar cache can accept zero rows. It nevertheless returns normally; frontend says Imported. Separate bridge-profile paths can subsequently hide imported bars when switching SIMULATOR/MT5. These are code-proven causes consistent with the operator report, not a claimed reproduction against their unavailable Windows cache.

The fix now commits the full validated frame, allows head/internal inserts, verifies persisted readback/counts and uses an explicitly configured canonical store for these attached pairs independent of active bridge. Partition/metadata transaction failures roll back. Post-commit receipt failures explicitly return data_committed=true with ok=false, never pretend no data was saved or that the whole operation completed. Existing old profile histories are preserved, never silently reinterpreted/merged.

## Actual inputs and timezone evidence
All7 files are tab-separated MT5 headered exports. Counts: M1 100057; M5 100012; M15 100856; M30 71441; H1 35738; H4 9349; D1 1560. Dominant spacing matches their intended timeframes. Coverage differs: M1 July2026 onward, M5 May2025 onward, M15 July2022 onward, M30/H1/H4/D1 October2020 onward. Intraday daily bounds generally01:00–23:xx; weekday-only source dates and daily/weekend closures support but do NOT prove server time. H4/D1 timestamps label broker bars, not necessarily the instant of the first trade in the bucket.

Official sources: https://www.icmarkets.com/global/en/help-resources/help-centre documents GMT+2/GMT+3 chart/server time aligned to New York close. https://www.icmarkets.bs/en/help-resources/help-centre additionally documents US second-Sunday-March/first-Sunday-November seasonal convention and XAUUSD01:02–23:57 server hours. Current published hours are not proof of every historical session; observed01:00 starts and occasional21:xx closes show historical/missing-bar uncertainty. Europe/Athens is NOT substituted for the US-transition server rule.

The user explicitly selected **Confirm IC Markets server time for all7 files** in this task. Operational UTC = source wall clock minus2h in US standard time or3h in US daylight time, using historical America/New_York DST rules, not one fixed offset. Originals remain byte-identical. This is explicit user confirmation of a convention supported by general broker documentation, not account-specific conclusive timezone verification. Fixed UTC, UTC+5/Pakistan and computer-local interpretations are not silently applied.


## Independently verified persistent baseline

All419,013 rows pass the actual validator: finite valid OHLC, ordered input, zero rejected rows and zero duplicate timestamps in every file. The MT5 headers are `<DATE>`, optional `<TIME>`, `<OPEN>`, `<HIGH>`, `<LOW>`, `<CLOSE>`, `<TICKVOL>`, `<VOL>`, `<SPREAD>`; delimiter is a tab. The instrument is not embedded in these candle rows: XAUUSD and TF mapping are the explicitly supplied mapping corroborated by intervals, not a claim that the filename proves instrument identity.

| TF | Unique persisted bars | First UTC bar-open | Last UTC bar-open | Nominal-grid gap intervals |
|---|---:|---|---|---:|
| M1 | 100,057 | 2026-07-07T18:41:00Z | 2026-10-16T12:31:00Z | 75 |
| M5 | 100,012 | 2025-05-21T07:35:00Z | 2026-10-16T12:30:00Z | 372 |
| M15 | 100,856 | 2022-07-12T22:00:00Z | 2026-10-16T10:45:00Z | 1,106 |
| M30 | 71,441 | 2020-09-30T22:00:00Z | 2026-10-16T12:30:00Z | 1,563 |
| H1 | 35,738 | 2020-09-30T22:00:00Z | 2026-10-16T12:00:00Z | 1,561 |
| H4 | 9,349 | 2020-09-30T21:00:00Z | 2026-10-16T09:00:00Z | 325 |
| D1 | 1,560 | 2020-09-30T21:00:00Z | 2026-10-15T21:00:00Z | 319 |

“Gap” is a discontinuity against nominal TF spacing, **not proof of corrupt/missing broker history**. It includes daily maintenance, weekends, historical holidays and some small unexplained holes. The manifest preserves the interval distribution and sample missing periods with truncation explicitly labelled; no OHLC is fabricated to fill these slots. Broker H4/D1 labels are preserved after conversion, not resampled or snapped to a UTC grid. No claim is made that present-day published trading hours prove every historical closure.

Independent stdlib CSV/datetime/ZoneInfo parsing compared **every timestamp, OHLC, tick-volume, real-volume and spread value** to a separate read of the durable monthly partitions. Each original is checked against the attachment's SHA-256. Actual canonical totals are read from metadata, not hardcoded into the program. Current original-file digests are in the versioned manifest and Git LFS pointer objects.

## UI → persistent store → new engine proof

The final implementation was exercised through the actual HTTP validate/commit routes with the attached100,856-row M15 file and an isolated recent2,698-row overlap **drawn from that same real file**. This reproduces the overlap shape without claiming access to the operator's Windows cache:

- Validation:100,856 valid,0 rejected;2,698 existing/overlapping.
- Commit:98,158 newly inserted,2,698 identical duplicates,0 conflicts,100,856 persistent total.
- Validation0.769s; archive+store+readback commit1.107s in this run.
- Exact original archive bytes verified; a reopened store and fresh DataEngine both return100,856 bars under `XAUUSD_M15_USER_HISTORY_ICMARKETS_CANONICAL_v2`, even with a simulator active profile.
- The separate real persisted Node718 Deep proof consumes canonical M1 candles using the existing strategy/backtester. Original generation records and qualified population are unchanged.

The validation token retains the complete validated frame; only the displayed preview is bounded. Changing the source timezone clears the token and forces revalidation. The UI offers the confirmed IC seasonal preset, named IANA zones and explicitly bounded fixed offsets, with source→UTC examples. Existing old-profile data is not retroactively shifted.

Original bytes are archived atomically **before** a candle merge, so an original-archive failure cannot leave an unarchived successful import. Per-store thread/process file locks serialize partition writes, reads, crash recovery and canonical materialization. A journal is committed only after partition/metadata work succeeds. A failed final receipt is explicitly a partial `ok=false,data_committed=true` result; safe retry dedupes the already-saved bars and completes provenance. It is not falsely described as an all-or-nothing transaction across SQLite, every JSON receipt and the filesystem.

Full reproduction evidence and final gate/source identity are linked in `V9_2_REPORT.md`. Native terminal/server availability remains unverified.
