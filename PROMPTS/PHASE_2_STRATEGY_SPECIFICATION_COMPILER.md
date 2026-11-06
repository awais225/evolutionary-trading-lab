# PROMPT — HERO1 — PHASE 2: STRATEGY SPECIFICATION, VALIDATION & COMPILER

> Verbatim task text as received. This message contained no credentials.
> Edit nothing above the OUTCOME marker.

HERO1 — PHASE 2: STRATEGY SPECIFICATION, VALIDATION & COMPILER

**Target release:** HERO V1.1.0  
**Repository:** `awais225/evolutionary-trading-lab`  
**Branch:** `main`  
**Baseline commit:** `396a71e4de7ff96994da2342a38157a53a24a7d0`  
**Previous release:** HERO-V1.0.0  
**Development environment:** Existing Hero1 Linux development environment  
**Production DATA:** Outside the repository, never use for development tests.

1. FIRST: VERIFY THE ACTUAL BASELINE

Before modifying anything:

1. Inspect the current GitHub `main` branch and pull the latest changes.
2. Verify that the checked-out HEAD matches the current remote `main`.
3. Confirm that the existing repository is `awais225/evolutionary-trading-lab`.
4. Confirm that `legacy-archive` remains untouched.
5. Inspect the existing implementation and documentation, particularly:

- `packages/strategy_spec/`
- `packages/exec_semantics/`
- `packages/indicators/`
- `packages/storage/src/hero1_storage/instrument_models.py`
- `packages/storage/src/hero1_storage/repositories.py`
- `services/api/app/schemas.py`
- `services/api/app/api/routers/nodes.py`
- `apps/web/src/pages/ManualNodes.tsx`
- `apps/web/src/api/client.ts`
- `apps/web/src/api/types.ts`
- `tests/`
- `docs/PHASE_ROADMAP.md`
- `CHATGPT.txt`

The strategy specification package is currently a skeleton. Do not assume functionality exists just because a package directory exists.

Record the actual baseline and existing tests before making changes.

2. PRIMARY OBJECTIVE

Build the foundational strategy specification system that converts a human-written or AI-generated strategy into a structured, validated, versioned and deterministic representation.

The central architectural principle:

**A strategy is structured DATA, never executable Python code.**

Every strategy, regardless of its origin, must pass through the same validation and compilation pipeline.

Supported origins:

- CUSTOM
- WWW
- INTERACTIVE
- FINE_TUNED

Phase 2 must establish the common contract that all future strategy discovery and testing systems will use.

3. STRATEGY SPECIFICATION PACKAGE

Implement the existing package:

`packages/strategy_spec/src/hero1_strategy_spec/`

Use Pydantic v2 models and strict validation.

Create a versioned specification schema with at least these logical sections:

A. Identity

- schema_version
- strategy name
- description
- instrument
- timeframe
- strategy direction: LONG / SHORT / BOTH
- origin
- strategy metadata

B. Entry conditions

Support nested logical expressions:

- AND
- OR
- NOT

Support comparison operators:

- greater than
- greater than or equal
- less than
- less than or equal
- equal
- not equal
- cross above
- cross below

Support operands such as:

- OHLC candle values
- previous candle values
- indicator references
- numeric constants

Conditions must be represented as a typed expression tree, not strings containing Python expressions.

C. Exit conditions

Support:

- stop loss
- take profit
- explicit exit conditions
- maximum holding duration
- optional time-based exit
- long and short direction semantics

Every strategy must explicitly declare its exit behavior.

Do not silently invent missing exits.

D. Indicator references

Create a typed indicator reference contract.

Potential initial supported identifiers:

- SMA
- EMA
- RSI
- MACD
- Bollinger Bands
- ATR
- VWAP
- ROC
- Stochastic
- ADX

However:

- Do not implement their mathematical calculations in Phase 2.
- Do not duplicate the future Phase 3 indicator engine.
- Implement metadata and parameter validation only for the indicators actually supported in this phase.
- Unknown indicators must produce structured validation errors.
- Indicator parameters must be checked against their declared constraints.
- Define required historical warm-up information where possible.

E. Risk and position configuration

Support:

- stop-loss definition
- take-profit definition
- risk/reward configuration
- position sizing mode
- fixed monetary risk or fixed volume
- maximum holding period
- direction-specific risk configuration

Distance units must be explicit.

Possible units include:

- absolute price distance
- broker points
- ticks
- pips
- ATR multiples

Never assume that one pip or tick has the same meaning across XAUUSD, EURUSD and BTCUSD.

If broker metadata is missing, validation must report that the relevant risk definition cannot yet be resolved. Do not invent a default.

F. Execution configuration

Define declarative execution intent only:

- entry timing
- completed candle versus forming candle
- next-bar execution versus close execution
- permitted order type
- position handling assumptions

Do not implement actual order placement or a backtesting engine.

4. CANDLE INDEXING AND LOOKAHEAD SAFETY

This is a critical requirement.

Define explicit candle references:

- current completed candle
- previous completed candle
- two candles ago
- forming candle, only when explicitly permitted

Every expression must have deterministic indexing semantics.

Rules:

1. A completed-candle strategy must not accidentally read future data.
2. Cross-above and cross-below must compare the correct current and previous values.
3. Forming candles must not be silently treated as completed.
4. Indicator warm-up requirements must be explicit.
5. Invalid or impossible candle references must fail validation.
6. Compilation must not introduce lookahead bias.

Add synthetic tests proving these rules.

5. VALIDATION ENGINE

Implement a centralized validator inside the strategy specification package.

Validation must have multiple layers:

1. Structural validation: required fields, data types and schema version.
2. Semantic validation: valid expression trees, logical operators and operand compatibility.
3. Instrument validation: the strategy instrument must match its owning instrument context.
4. Timeframe validation: only supported timeframes.
5. Indicator validation: supported identifiers and valid parameters.
6. Risk validation: valid values, units and required metadata.
7. Execution validation: valid entry and exit semantics.
8. Safety validation: reject arbitrary code, unsupported operators and unbounded expressions.

Return structured errors, not generic strings.

Each error should include:

- error code
- field or expression path
- message
- severity
- optional remediation guidance

Distinguish between:

- INVALID: cannot be accepted.
- WARNING: accepted with a known limitation.
- NEEDS_METADATA: cannot fully validate until required broker metadata exists.
- VALID: all required checks passed.

A strategy that needs unavailable broker metadata must never be silently marked fully valid.

6. DETERMINISTIC COMPILER

Implement a deterministic compiler that converts a valid specification into a normalized internal representation (IR).

Requirements:

- Canonical JSON serialization.
- Stable ordering of keys and normalized values.
- Deterministic SHA-256 fingerprint.
- Identical specifications must produce identical fingerprints.
- Different meaningful specifications must produce different fingerprints.
- Compiler output must be independent of dictionary insertion order.
- Compiler must preserve strategy semantics.
- Compiler must record schema and compiler versions.

Absolutely prohibited:

- `eval()`
- `exec()`
- dynamic Python imports generated from strategy content
- executing AI-generated Python
- arbitrary shell commands
- interpreting strategy expressions as source code

The IR is a declarative representation for future Phase 3/4 consumers. It is not an execution engine.

Add determinism and security tests.

7. BULLISH ENGULFING REFERENCE STRATEGY

Implement one complete example specification and use it as a test fixture.

Strategy:

**Bullish Engulfing**

Entry conditions:

1. Previous completed candle is bearish.
2. Current completed candle is bullish.
3. Current candle body engulfs the previous candle body.
4. Current high is greater than previous high.
5. Current low is lower than previous low.

Entry timing:

- Evaluate only after the signal candle closes.
- Explicitly define whether execution occurs at the next bar open.

Stop loss:

- Below the signal candle low.
- Buffer: 10 pips.

Take profit:

- Risk/reward ratio: 1:2.

Create separate example specifications for:

- XAUUSD M1
- XAUUSD M5
- XAUUSD M15

Important:

Do not hardcode a universal XAUUSD pip size.

The specification must preserve the requested 10-pip buffer, but validation must require an explicit, trustworthy pip-size source. If it is not available, return NEEDS_METADATA rather than guessing.

The example must demonstrate that the same strategy logic can be represented on different timeframes without mixing instrument or timeframe context.

8. DATABASE AND NODE VERSIONING

Reuse the existing database architecture.

The existing `Node` model already contains:

- spec_json
- spec_hash
- version
- parent_node_id

The existing `NodeLineage` model already records lineage.

Do not create a duplicate strategy table or a second node registry.

Inspect whether the existing columns and relationships are sufficient. Add an Alembic migration only if a genuinely necessary field or constraint is missing.

Critical immutability requirement

The current node PATCH endpoint permits changes to `spec_json` without automatically updating the specification hash or version.

Correct this.

A saved strategy specification must be immutable.

When a user edits a strategy:

1. Validate the new specification.
2. Compile it.
3. Create a new node version.
4. Link it to the previous version.
5. Increment the version within its lineage.
6. Preserve the old specification and fingerprint.
7. Record the relationship in `node_lineage`.

Do not overwrite historical strategy definitions.

Ordinary metadata edits may remain available where appropriate, but changes to strategy semantics must create a new version.

Validate that a parent node exists in the same instrument database before creating a child.

Prevent cross-instrument lineage.

9. API INTEGRATION

Extend the existing node API rather than creating a parallel API.

Existing base route:

`/api/v1/instruments/{instrument}/nodes`

Implement the necessary endpoints for:

- creating a strategy node with a specification
- retrieving a node and its complete specification
- listing nodes
- validating a specification without saving it
- compiling a valid specification
- creating a new immutable version of an existing node

Use Pydantic request and response schemas.

Important existing issue:

`NodeOut` currently omits `spec_json`.

Correct this so that saved strategy specifications can be retrieved.

Do not accept a client-supplied hash as authoritative. The server must calculate the canonical fingerprint.

Keep explicit instrument context mandatory on every instrument-scoped endpoint.

Unknown instruments must fail before their database is opened.

All responses must retain the established structured error conventions.

Publish appropriate events through the existing EventBus for:

- specification validation
- compilation
- node creation
- new version creation
- validation failure

Do not add database polling.

10. MANUAL NODES FRONTEND

Implement a focused Phase 2 integration in the existing Manual Nodes page.

Do not build the complete visual strategy builder yet.

Required functionality:

1. Create a node draft.
2. Select the active instrument.
3. Select a timeframe.
4. Load the bullish engulfing example.
5. View and edit the structured specification.
6. Validate the specification.
7. Display structured validation errors and warnings.
8. Compile the specification.
9. Display its normalized IR and SHA-256 fingerprint.
10. Save the validated specification.
11. Retrieve and inspect saved specifications.
12. Create a new version without destroying the previous version.
13. Display version and lineage information.

Use the existing instrument context, API client, design system and error/loading states.

Do not redesign the overall dashboard or modify unrelated pages.

Do not display fake compilation results or pretend a strategy has been backtested.

11. TESTING REQUIREMENTS

Use synthetic data and temporary database roots only.

Never access, modify, delete or reset production DATA.

Add tests for:

Specification

- valid specification acceptance
- missing required fields
- invalid schema versions
- invalid logical expressions
- unsupported operators
- invalid timeframe
- invalid instrument
- unsupported indicators
- invalid indicator parameters
- invalid risk definitions

Compiler

- deterministic output
- deterministic hash
- different specifications produce different hashes
- canonicalization
- malformed expression rejection
- arbitrary code rejection
- no execution of generated content

Candle semantics

- current versus previous candle indexing
- completed versus forming candle behavior
- engulfing conditions
- lookahead prevention
- indicator warm-up metadata

Persistence

- create and retrieve complete specifications
- hash generated by server
- immutable historical versions
- version increment
- parent/child lineage
- cross-instrument isolation
- invalid parent rejection

API

- validate without persistence
- compile valid specification
- reject invalid specification
- create node
- retrieve specification
- create new version
- preserve existing API compatibility

Frontend

- create draft
- load example
- edit specification
- show validation errors
- compile and display IR
- save and retrieve specification
- create new version

Run the full existing backend and frontend test suites, not only the new tests.

Run the production frontend build.

Do not weaken, delete or skip existing tests to achieve a passing result.

12. DOCUMENTATION AND PROJECT HANDOFF

Update documentation in the same commit as implementation.

Mandatory files:

- `CHATGPT.txt`
- `CHANGELOG.md`
- `docs/ARCHITECTURE.md`
- `docs/ISSUES_AND_FIXES.md`
- `docs/PHASE_ROADMAP.md`
- `PROMPTS/README.md`

Archive this exact prompt before implementation as:

`PROMPTS/PHASE_2_STRATEGY_SPECIFICATION_COMPILER.md`

Do not rewrite the original prompt after execution. Append a separate OUTCOME section.

Correct existing documentation inconsistencies

The current handoff contains outdated statements that the existing repository is legacy-only and must never be modified.

Correct these statements:

- `awais225/evolutionary-trading-lab` is the active Hero1 repository.
- `main` hosts Hero1.
- `legacy-archive` preserves the original legacy project.
- Hero1's current main commit and release tag must be accurately recorded.
- GitHub publishing is no longer blocked.
- Update the stale architecture and development documentation that still describes the repository as having no remote or publishing as blocked.

Preserve the historical development log. Do not delete old entries or rewrite history.

Document the actual Phase 2 implementation and explicitly distinguish implemented, partial, planned, unverified and unsupported features.

13. STRICT SCOPE BOUNDARIES

This phase is ONLY about the strategy specification, validation, deterministic compilation, node versioning and focused Manual Nodes integration.

Do not implement:

- Full visual drag-and-drop strategy builder.
- Actual indicator calculations.
- Market data ingestion or synchronization.
- Backtesting.
- DeepTesting.
- Out-of-sample testing.
- Walk-forward testing.
- Fine tuning.
- WWW Research.
- Interactive WWW research agent.
- AI autonomous strategy generation.
- MT5 order execution.
- Paper trading.
- Live trading.
- GPU optimization.
- Dashboard redesign.
- New database architecture.
- Production DATA migrations or resets.

Do not modify `legacy-archive`, legacy branches or legacy tags.

Do not add production DATA, databases, logs, models, credentials or secrets to Git.

If a requirement cannot be implemented safely within this scope, document it and stop rather than silently expanding the phase.

14. VERSION CONTROL AND RELEASE

Target release: `HERO-V1.1.0`

Before committing:

1. Run all backend tests.
2. Run all frontend tests.
3. Run the frontend production build.
4. Run lint/type checks where configured.
5. Inspect the complete Git diff.
6. Confirm no DATA, databases, secrets or credentials are staged.
7. Verify documentation consistency.
8. Confirm the legacy archive branch has not been changed.

Update all required documentation in the same implementation commit.

Commit with a clear message, for example:

`HERO V1.1.0: strategy specification and deterministic compiler`

Push to the existing repository's `main` branch using the securely configured GitHub authentication mechanism.

Never use a token pasted into chat or save credentials in source code, scripts, logs or Git configuration.

After pushing:

- Verify remote `main` SHA.
- Verify the release tag points to the intended release commit.
- Verify `legacy-archive` remains unchanged.
- Record exact commit SHA and tag in `CHATGPT.txt` and `CHANGELOG.md`.
- Push any final documentation commit and verify it remotely too.

Create the `HERO-V1.1.0` release tag only if all release requirements pass.

15. FINAL REPORT REQUIRED

When finished, provide:

1. Baseline commit SHA.
2. Final commit SHA.
3. Release tag and target SHA.
4. Files created and modified.
5. Strategy specification structure.
6. Validation capabilities.
7. Compiler and fingerprint behavior.
8. Database changes and migrations, if any.
9. API endpoints added or changed.
10. Frontend features completed.
11. Backend test results.
12. Frontend test results.
13. Production build result.
14. GitHub remote verification.
15. Confirmation that `legacy-archive` was untouched.
16. Any incomplete features, limitations or known issues.
17. Exact next recommended development phase.

Be completely honest about test coverage and verification. A passing unit test does not prove actual trading performance or Windows/MT5 compatibility.

**STOP AFTER PHASE 2. Do not begin Phase 3 without explicit authorization.**

---

## OUTCOME (2026-11-06 — HERO-V1.1.0)

Executed as authorized, stopped at Phase 2 scope. Verified results:

- Baseline: `396a71e4de7ff96994da2342a38157a53a24a7d0` (main == origin/main at
  start; HERO-V1.0.0 record commit).
- Release commit: `ef6742e916a02e9fdf938cbb2fea531523f507fe`
- Tag: `HERO-V1.1.0` → ef6742e91 (annotated tag object
  `f64bb18be15cba16b7550fdfb40631cc7da3dd72`)
- Tests: backend 86 passed / 1 skipped (Ollama unreachable — by design);
  frontend 9 passed; production build OK (`tsc --noEmit && vite build`).
- Live smoke (uvicorn, temp data root :8799): validate w/o broker meta →
  NEEDS_METADATA; validate w/ meta → VALID (warmup 1); compile → fingerprint
  eb132930ec… (matches unit tests); create → 201 server-hashed; new version →
  v2 parent #1; lineage [v1, v2]. PASS.
- Missing broker metadata never guessed → NEEDS_METADATA;
  client-supplied fingerprints never authoritative (server recomputes);
  semantic spec changes create immutable parent-linked versions; PATCH is
  metadata-only; duplicate spec at same version → 409 SPEC_DUPLICATE;
  cross-instrument versioning rejected.
- Database: NO schema change, NO migration (Node/NodeLineage reused;
  immutability is application-level). Production DATA untouched.
- Push: BLOCKED — no securely configured GitHub authentication exists in this
  environment; per protocol, no chat-pasted token was used. Remote remains at
  the Phase 1.3 state until the owner configures secure auth.
- Windows / RTX 4090 / MT5 / Ollama: NOT verified here. Unit tests ≠ trading
  performance or hardware compatibility.
- Phase 3 (market data & indicator engine) NOT started — awaits explicit
  authorization.
