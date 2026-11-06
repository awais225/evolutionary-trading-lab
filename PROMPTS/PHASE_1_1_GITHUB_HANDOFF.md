# PROMPT — HERO1 TASK 2.1: PUBLISH PHASE 1 TO GITHUB + ESTABLISH PERMANENT PROJECT HANDOFF DOCUMENTATION

> Verbatim development prompt, preserved exactly as received (Unicode as-is).
> Edit nothing above the OUTCOME marker.

HERO1 TASK 2.1: PUBLISH PHASE 1 TO GITHUB + ESTABLISH PERMANENT PROJECT HANDOFF DOCUMENTATION

Objective

Publish the existing Hero1 Phase 1 project to a new private GitHub repository and establish a strict documentation and handoff process that must be followed at every future development, commit, and push stage.

Existing local project:

`/home/user/hero1-trading-platform`

Current branch: `main`

Existing Phase 1 commit:

`840bf71d9d3adbb556be1bf19771b63d98ae3e57`

Expected GitHub repository:

`hero1-trading-platform`

The old repository `awais225/evolutionary-trading-lab` must remain completely untouched.

PART 1: GITHUB AUTHENTICATION AND REPOSITORY

1. Inspect the current Git configuration, repository status, branch, remotes and existing commit history.
2. Check whether a securely configured GitHub connector or platform-provisioned credential is now available.
3. Do not use, retrieve, reproduce, or embed any credential previously pasted into chat.
4. Never request a GitHub PAT in chat, write credentials into source files, place tokens in Git URLs, or expose credentials in logs.
5. If secure authentication is available:
  - Create `hero1-trading-platform` as a PRIVATE GitHub repository if it does not already exist.
  - If it already exists, inspect it before pushing. Do not overwrite unrelated content or history.
  - Configure the local repository's `origin` to the correct private repository.
  - Preserve the existing `main` branch and root commit.
  - Publish the complete existing Phase 1 history.
6. If secure authentication is unavailable, do not pretend the repository was pushed. Report the exact limitation and leave the local project intact.

Do not change or delete the old repository.

PART 2: MANDATORY DOCUMENTATION SYSTEM

Before publishing, inspect existing documentation and update it without destroying useful historical information.

The following files must exist in the Hero1 repository:

A. `CHATGPT.txt` — Main project handoff

This is the primary handoff document between LMS Agent and ChatGPT.

It must be written so that ChatGPT can understand the complete current project state without needing to reconstruct it from the entire conversation.

Maintain these sections:

1. Project identity and objective.
2. Current version and development phase.
3. Current GitHub repository, branch and latest commit SHA.
4. Project architecture and technology stack.
5. Complete folder structure and important source files.
6. Database architecture and instrument isolation.
7. Current application features and their implementation status.
8. Known issues and unresolved problems.
9. Issues resolved in each version, including the actual solution and relevant source files.
10. Important architectural decisions and reasons.
11. Tests performed, exact commands, results and limitations.
12. Windows and RTX 4090 verification status.
13. Ollama and AI model integration status.
14. MT5 integration status.
15. DATA protection and storage rules.
16. Current development priorities.
17. Next authorized task and phase boundary.
18. Important warnings and things that must not be changed.

Keep this file concise enough to be useful, but sufficiently detailed to act as a technical handoff.

**Update it at every development stage. Never replace the entire file with a short summary. Preserve historical information and append or revise the appropriate sections.**

B. `CHANGELOG.md` — Version history

Record every completed development stage and release.

For each version include:

- Version identifier.
- Date.
- Development phase.
- Features introduced.
- Bugs and issues resolved.
- Technical changes and affected modules.
- Tests performed and results.
- Known limitations.
- Git commit SHA.
- Git tag, if one was created.

Never claim an issue was resolved unless the fix was implemented and verified.

C. `docs/ISSUES_AND_FIXES.md` — Technical issue register

Maintain a structured record of:

- Issue ID.
- Date discovered.
- Affected feature or module.
- Original symptoms.
- Root cause, if established.
- Implemented solution.
- Files changed.
- Verification method and outcome.
- Version and commit containing the fix.
- Current status: open, resolved, deferred or blocked.

Do not erase resolved issues. Preserve them as historical records.

D. `PROMPTS/` — Development prompt archive

Create a permanent prompt archive.

Store the exact development prompts used for each authorized task, including:

- Phase 1 foundation.
- Phase 1.1 GitHub publishing and documentation.
- Every future Phase 2 and subsequent task.

Use descriptive filenames, for example:

`PROMPTS/PHASE_1_FOUNDATION.md`

`PROMPTS/PHASE_1_1_GITHUB_HANDOFF.md`

`PROMPTS/PHASE_2_STRATEGY_ENGINE.md`

For future tasks, save the exact prompt before executing the task. After completion, update its file with the task outcome in a separate clearly marked section. Do not silently rewrite the original instructions.

E. `docs/ARCHITECTURE.md`

Maintain the actual implemented architecture, not just the planned architecture.

Distinguish clearly between:

- Implemented.
- Partially implemented.
- Planned.
- Unverified.
- Not supported.

Keep the document synchronized with actual source code.

PART 3: MANDATORY RULE FOR EVERY FUTURE COMMIT AND PUSH

This is a permanent project rule.

Before every commit:

1. Update `CHATGPT.txt`.
2. Update `CHANGELOG.md`.
3. Update `docs/ISSUES_AND_FIXES.md` if an issue was discovered, addressed or deferred.
4. Archive the exact development prompt in `PROMPTS/`.
5. Update `docs/ARCHITECTURE.md` whenever architecture changes.
6. Run relevant tests.
7. Check Git status and inspect the staged diff.
8. Verify that no credentials, `.env` files, databases, production DATA, generated datasets or private logs are accidentally staged.

Documentation updates must be included in the same commit as the code changes they describe.

After every successful push:

- Record the final commit SHA.
- Record the branch and version.
- Record the push result.
- Update the handoff documentation with the published SHA.
- If updating the SHA requires another commit, ensure that commit is also pushed.

Never report a push as successful based only on a local commit. Verify the remote branch and commit using GitHub or `git ls-remote` with the authenticated connection.

PART 4: PHASE 1.1 VERSIONING

Treat the existing Phase 1 commit as the original foundation:

`840bf71d9d3adbb556be1bf19771b63d98ae3e57`

Do not rewrite it.

Use a new commit for the documentation and GitHub publishing preparation.

If the project does not already have a versioning convention, establish a sensible initial version such as `v0.1.0` for the Phase 1 foundation. Do not create conflicting tags or change existing tags.

Keep all future releases traceable to their exact commits.

PART 5: STRICT PROTECTION RULES

- Do not modify the old Evolutionary Trading Lab repository.
- Do not delete, migrate, reset, overwrite or regenerate any existing production DATA.
- Do not create or commit actual production DATA in Hero1.
- Do not introduce real MT5 orders.
- Do not begin Phase 2 or implement additional application functionality.
- Do not make unrelated refactoring changes.
- Do not claim Windows, RTX 4090 or MT5 verification without actual tests on the relevant hardware/environment.
- Do not include credentials, API keys, passwords or private account details in handoff files.
- Keep the repository private.

PART 6: FINAL DELIVERY REPORT

After completing the work, provide:

1. GitHub repository URL and confirmation that it is private.
2. Confirmation that `main` was pushed.
3. Exact original Phase 1 commit SHA.
4. Exact new documentation commit SHA.
5. GitHub remote verification result.
6. Final repository tree showing the documentation files.
7. Summary of each handoff document and its purpose.
8. Confirmation that the old repository was untouched.
9. Confirmation that no production DATA or credentials were committed.
10. Any remaining blockers.

If GitHub authentication is still unavailable, complete the documentation locally, commit the changes locally, and clearly report that publishing remains blocked.

**STOP after this task. Do not start Phase 2 until explicitly authorized.**

---

## OUTCOME (2026-11-06 · v0.1.1 · commit: see Git log / CHATGPT.txt §19)

PART 1 — authentication: connector "unsupported"; no GITHUB_TOKEN/gh/helpers →
PUSH BLOCKED. Local git verified: main @ 840bf71, no remotes, clean tree before
docs. Old repository untouched. Repository NOT created (would require secure
auth). No tokens requested, embedded, or reproduced.

PART 2 — documentation created/updated locally:
- CHATGPT.txt (all 18 sections + append-only Phase 1.1 log)
- CHANGELOG.md (v0.1.0 foundation, v0.1.1 documentation)
- docs/ISSUES_AND_FIXES.md (ISSUE-001..011; 010 blocked, 009 deferred, 011 deferred)
- PROMPTS/ (README, PHASE_1_FOUNDATION [flagged reconstruction], this file verbatim)
- docs/ARCHITECTURE.md (status-tagged implementation truth pass)

PART 3 — permanent pre/post-commit protocol written into CHATGPT.txt §17–19 and
CHANGELOG.md header; applied in this commit itself (docs shipped WITH the
prepared-for-publishing state; no code changed).

PART 4 — versioning established: tag v0.1.0 → 840bf71 (Phase 1 foundation);
v0.1.1 → Phase 1.1 docs commit (local tags; push blocked).

PART 5 — protections honored (see CHATGPT.txt §18).

PART 6 — final delivery report delivered in chat on 2026-11-06. Publishing
remains blocked on owner-provided secure credentials. STOP honored — Phase 2
not started.
