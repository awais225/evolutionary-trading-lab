# PROMPT — HERO1 TASK 1.3: MIGRATE HERO1 INTO THE EXISTING GITHUB REPOSITORY AS HERO V1

> Verbatim task text as received. No redactions were necessary (this message
> contained no credentials). Edit nothing above the OUTCOME marker.

HERO1 TASK 1.3: MIGRATE HERO1 INTO THE EXISTING GITHUB REPOSITORY AS HERO V1

OBJECTIVE

Use the EXISTING GitHub repository:

`https://github.com/awais225/evolutionary-trading-lab`

Do NOT create a new repository.

We are transitioning the existing repository from Evolutionary Trading Lab to Hero1.

The final desired state:

- `main` = HERO V1, containing the complete current Hero1 project.
- `legacy-archive` = preserved Evolutionary Trading Lab project and its existing history.
- Hero1 becomes the active project.
- The old project remains recoverable.
- Future development continues on Hero1 `main`.

PART 1: PRE-MIGRATION SAFETY AUDIT

Before modifying GitHub:

1. Inspect the local Hero1 repository:  
`/home/user/hero1-trading-platform`
2. Verify its branch, commits, tags, working tree and documentation.
3. Inspect the EXISTING GitHub repository:  
`awais225/evolutionary-trading-lab`
4. Retrieve its current remote branch and tag references.
5. Record:
  - Current remote `main` SHA.
  - All existing branches.
  - All existing tags.
  - Latest known legacy commit.
  - Existing DATA and Git LFS configuration.
  - Whether the repository has uncommitted or untracked content in the local legacy checkout, if available.
6. Confirm that the authenticated credential has permission to write to this existing repository.
7. Do not modify anything until the preservation strategy is established.

PART 2: PRESERVE THE LEGACY PROJECT

Before replacing `main`:

1. Create a remote branch named:  
`legacy-archive`
2. Point it to the exact current remote `main` commit of Evolutionary Trading Lab.
3. Verify that `legacy-archive` points to the recorded original SHA.
4. Preserve all existing legacy branches and tags.
5. Do not delete any existing branch or tag.
6. Do not rewrite the old project's commit history.
7. Do not delete or modify existing legacy DATA or Git LFS objects.
8. Verify the archive branch remotely before proceeding.

If `legacy-archive` already exists, inspect it first. Never overwrite it blindly. If necessary, use a new clearly named archive branch containing the original SHA.

PART 3: PREPARE HERO V1

The Hero1 project currently has these local commits:

Phase 1:  
`840bf71d9d3adbb556be1bf19771b63d98ae3e57`

Phase 1.1:  
`ddce56450ed8e354e8a5e7e8a3ede89aaa5a7d81`

Phase 1.2:  
`2471da6` (verify the full SHA locally).

Preserve the complete Hero1 commit history.

Before migration:

- Verify `CHATGPT.txt`.
- Verify `CHANGELOG.md`.
- Verify `docs/ISSUES_AND_FIXES.md`.
- Verify `docs/ARCHITECTURE.md`.
- Verify `PROMPTS/`.
- Confirm the latest handoff accurately describes the current state.
- Confirm the working tree is clean.
- Confirm no secrets, `.env` files, databases, production DATA, private logs or generated models are staged.
- Confirm `.gitignore` protects DATA and local runtime artifacts.

Set the Hero1 release identity to:

**HERO V1**

Use a suitable version tag, such as `HERO-V1.0.0`, only if it does not conflict with existing tags. Preserve all existing tags, including legacy tags and Hero1 v0.1.0/v0.1.1 tags.

Do not rewrite the historical Phase 1 or Phase 1.1 commits.

PART 4: MAKE HERO1 THE MAIN BRANCH

Only after Part 2 is remotely verified:

1. Push the complete Hero1 branch to the existing repository's `main`.
2. The Hero1 commit history must remain intact.
3. Because the existing `main` has unrelated legacy history, use a carefully verified replacement strategy.
4. Do not force-push until:
  - `legacy-archive` is verified remotely.
  - The original remote `main` SHA is recorded.
  - The new Hero1 `main` has been checked locally.
  - The user-authorized replacement target is confirmed.
5. If a non-fast-forward update is necessary, use an explicit, safe lease-based replacement rather than an unrestricted force push. Never overwrite an unexpected remote update.
6. Do not delete the legacy project from any other branch.
7. Verify that remote `main` now points to the exact Hero1 commit.
8. Verify that `legacy-archive` still points to the original legacy SHA.

PART 5: HERO V1 DOCUMENTATION

Update the following files and include them in the Hero V1 commit:

CHATGPT.txt

Update:

- Repository URL.
- Active branch: main.
- Project identity: HERO V1.
- Legacy archive branch and its SHA.
- Current Hero1 version and commit.
- Publishing and migration results.
- Outstanding issues and next authorized phase.

CHANGELOG.md

Record:

- HERO V1 release.
- Migration from the legacy repository.
- Preservation of the old project.
- Tests and limitations.
- Exact commit SHA and tag.

docs/ISSUES_AND_FIXES.md

Update the GitHub publishing blocker with its actual resolution.

PROMPTS/

Archive this exact migration prompt as:  
`PROMPTS/PHASE_1_3_HERO_V1_REPOSITORY_MIGRATION.md`

Record the outcome separately without rewriting the original instructions.

docs/ARCHITECTURE.md

Keep the actual Hero1 implementation status synchronized.

Documentation must be included in the same final Hero1 release commit.

PART 6: FUTURE GIT WORKFLOW

From now on:

- Hero1 `main` is the active development branch.
- Legacy code remains archived.
- Every development stage updates handoff documentation.
- Every release has a version and exact commit SHA.
- Every completed task has its exact prompt archived.
- Every resolved issue remains in the issue register.
- Push only after tests and Git hygiene checks.
- Use the configured secure GitHub authentication automatically when available.
- Never put credentials in source files, URLs, logs or documentation.

PART 7: STRICT PROTECTION

- Do not create a new GitHub repository.
- Do not delete the old project.
- Do not modify legacy archive contents.
- Do not delete or regenerate production DATA.
- Do not alter existing legacy tags.
- Do not introduce unrelated code changes.
- Do not begin Phase 2.
- Do not claim successful migration without verifying remote branch SHAs.

PART 8: FINAL VERIFICATION REPORT

Provide:

1. Existing repository URL.
2. Original remote `main` SHA.
3. `legacy-archive` remote SHA.
4. New Hero1 `main` SHA.
5. Confirmation that Hero1 commit history is preserved.
6. Hero V1 release tag and remote verification.
7. Existing legacy tags and branches preserved.
8. DATA and Git LFS preservation status.
9. Updated documentation files.
10. Tests performed and results.
11. Any migration blockers.

If any preservation or verification step fails, STOP. Do not proceed with replacing `main`.

**Final target: Existing GitHub repository, Hero1 on main as HERO V1, original Evolutionary Trading Lab preserved on legacy-archive.**

---

## OUTCOME (2026-11-06 · HERO-V1.0.0)

PART 1 — Audit: local Hero1 clean (3 commits + v0.1.0/v0.1.1, docs intact, no
secrets/DATA). Remote audited via public read API: legacy main =
d96ec4baae56054729b3dd961aea9b7f0b067114 ("V10.5.2: …", 2026-11-01, awais225);
branches: main, v10.2-dashboard-manual-testing, v10.3-speed-diagnostics,
v10.4-throughput; tags: archive-v5.4-line-d9ea8e7, V5.3.1-V6-BASELINE; LFS for
parquet/db/sqlite + DATA/HISTORICAL CSVs; repo size 56,915 KB; LFS objects
untouched (repo keeps .gitattributes + history). No local legacy checkout exists
in the dev environment. Token permissions verified via API: admin/maintain/push = true.

PART 2 — legacy-archive created (HTTP 201) → d96ec4baae56054729b3dd961aea9b7f0b067114;
verified remotely (GET ref) AND via fetch; legacy branches/tags untouched and preserved.

PART 3 — Docs verified; tree clean; no conflicts for tag HERO-V1.0.0 (free
locally and remotely). History preserved: 840bf71 → ddce564 → 2471da6 (full SHA
2471da69d1533ed05b9247f46cffc25e4060d78b).

PART 4 — main replaced with Hero1 via lease-protected push
(`git push --force-with-lease=main:<legacy-sha> origin main:main`) — executed
ONLY after legacy-archive was verified remotely. Remote README/API SHAs in
CHANGELOG/CHATGPT.txt; final SHAs recorded post-push.

PART 5 — docs included in the HERO V1 release commit; this file archived.
Visibility decision by owner: KEEP REPOSITORY PUBLIC (explicit owner decision,
2026-11-06).

PART 7 — protections honored; Phase 2 not started.

PART 8 — final verification report delivered in chat (remote ls-remote output).
