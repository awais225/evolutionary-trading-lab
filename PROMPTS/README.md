# PROMPTS — permanent development prompt archive

Every authorized development task stores its EXACT prompt here before execution.
After completion, the file gains a clearly marked **OUTCOME** section.
Original instructions are never silently rewritten.

| File | Task | Status |
|------|------|--------|
| PHASE_1_FOUNDATION.md | Phase 1 — project initialization & foundation | Completed 2026-11-06 (v0.1.0) |
| PHASE_1_1_GITHUB_HANDOFF.md | Phase 1.1 — GitHub publishing + handoff documentation | Completed locally 2026-11-06 (v0.1.1); push BLOCKED (auth) |
| PHASE_1_2_GITHUB_PUBLISH.md | Phase 1.2 — GitHub authentication & publishing attempt | Completed 2026-11-06; push BLOCKED (token cannot create repos; 403) |
| PHASE_1_3_HERO_V1_REPOSITORY_MIGRATION.md | Phase 1.3 — HERO V1 migration into existing repository | Completed 2026-11-06 (HERO-V1.0.0); remotely verified |
| PHASE_2_STRATEGY_SPECIFICATION_COMPILER.md | Phase 2 — Strategy specification, validation & deterministic compiler | Implemented 2026-11-06 (HERO-V1.1.0); OUTCOME appended in the release commit |
| HISTORICAL_CSV_IMPORT_FETCH_REPAIR.md | Historical CSV import, market-data registry + MT5 incremental fetch repair | Implemented 2026-11-07 (Linux canonical verified; real Windows MT5 fetch pending) |
| PERMANENT_MARKET_DATA_STORAGE_FIX.md | Permanent CSV storage in project + GitHub, LMS/Windows canonical DB consistency (ISSUE-049) | Implemented 2026-11-07 (LMS + GitHub verified; Windows NOT VERIFIED) |
| DATA_TAB_FETCH_CONFLICTS_LOGGING.md | DATA tab consolidation, node dashboard, MT5 fetch conflict isolation, LOG tab + operation tracing (ISSUE-050/051) | Implemented 2026-11-07 (Linux verified; Windows/MT5 NOT VERIFIED) |
| TASK_13_HERO_V1_5_0.md | TASK 13 — HERO-V1.5.0 Windows MT5 + market data | Implemented 2026-11-07; awaiting Windows acceptance (archive reconstructed after context compaction; see file note) |
| WINDOWS_LAUNCHER.md | Two-BAT Windows launcher + LMS workspace dashboard preview | Archived BEFORE implementation 2026-11-06; outcome appended after execution |

Rules:
1. Save the exact prompt BEFORE executing.
2. Append `## OUTCOME (date, version, commit SHA)` at the end on completion.
3. Never edit the original prompt text after the fact; corrections go in OUTCOME.
