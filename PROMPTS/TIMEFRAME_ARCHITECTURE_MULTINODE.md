HERO1 — TIMEFRAME ARCHITECTURE + NODE GENERATION + START.BAT FIX
Archived verbatim 2026-11-06. Key mandates:
- Timeframe = NODE property, never a global app setting. NO global timeframe
  dropdown beside Instrument (Instrument dropdown stays exactly as-is).
- Remove existing single-timeframe selector from Strategy spec workspace;
  replace with themed multi-select in node strategy creation flow.
- Multi-select: actual supported timeframe registry (MT5 adapter), individual
  ticks + Select All + Clear All + selected count + readable summary + no-
  timeframe validation + graphical theme. Default = existing default else M15.
- Generate ONE NODE PER selected timeframe (independent records: unique id,
  instrument+TF, own spec/fingerprint/validation/testing history/lineage).
- Timeframe is part of semantic identity: hash changes with TF, NOT with
  display name; lineage correct; safe migration, never silently invalidate.
- Auto-naming: first-available sequential C1/C2/C3 (gap reuse), 7 TFs → 7
  sequential names; concurrent-safe; inline rename (click→edit, Enter save,
  Escape cancel, empty/dup validation); display name ⟂ id ⟂ fingerprint.
- TF visibility/filtering/grouping in Manual Nodes, DeepTesting, Fine Tuning,
  Live Testing + other node views; Select All respects active filters;
  counts per timeframe; no fake categories.
- No fabricated market data/backtests: unavailable data ⇒ created TF/DATA
  UNAVAILABLE/AWAITING MARKET DATA state, not "validated".
- Tests G1..G13 (single/3/all TFs, naming, gap reuse, id/name uniqueness,
  TF assignment, isolation, TF in hash, rename-neutral hash, filter/group,
  DeepTesting selection respecting TF filters, migration preserves nodes).
- TASK 4: START.bat must actually work (legacy v10.4-throughput reference,
  diagnose precisely — current failure: window vanishes after "Verifying
  backend imports..."); full requirements listed; Windows execution honest
  status required.
- Docs: CHATGPT.txt, CHANGELOG.md, ISSUES_AND_FIXES.md, ARCHITECTURE.md,
  prompt archive, IMPLEMENTATION_PROGRESS.md at each milestone.
- Commit+push to existing repo if authorized/successful.
