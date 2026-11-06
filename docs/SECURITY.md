# Security

## Secrets & credentials
- **Never commit secrets.** `.gitignore` blocks `.env`, `.env.*` (except
  `.env.example`), databases, logs, binaries, key material and venvs.
- `.env.example` contains dummies only. Real values live in your shell or in
  Windows user/machine environment variables (`HERO1_*`).
- MT5 passwords and any provider API keys belong in the OS credential store or
  environment — never in files, never in the UI, never in logs. The UI only
  ever displays whether they are configured.
- Any personal access token pasted into a chat or issue must be treated as
  compromised: revoke it at <https://github.com/settings/tokens> immediately.
  (This project was bootstrapped without any token; pushing remains a manual
  user action until secure auth is intentionally configured.)

## Repository hygiene (enforced by tests)
`tests/test_repo_hygiene.py` fails the build if: a `DATA/` tree or `*.db*`
file exists in the repo, `.env`/`*.pem`/`id_rsa*` is tracked, or tracked text
files match credential-shaped patterns (`ghp_`, `AKIA…`, `-----BEGIN … PRIVATE KEY`).

## Web surface
- CORS: localhost dev origins only (`5173/4173`). No wildcard origins.
- Errors: 500s return a generic body; stack traces stay in server logs.
- All instrument-scoped routes validate the symbol (422/404) — no silent defaults.
- Settings PUT accepts a known key whitelist with type/range validation.

## Data protection
- `HERO1_DATA_ROOT` must be outside the repository; the app refuses otherwise.
- Backups: stop services, copy `DATA/` (registry + per-instrument DBs + MARKET_DATA).
  Test restores quarterly.

## AI boundaries (constitutional)
- AI output is **never** executed as Python or shell.
- AI cannot grant approvals, arm live trading, or bypass risk gates.
- AI proposals become **child nodes**; live adoption requires the same explicit
  Approval record as human-authored strategies.
- Fine Tuning / Research agents run under token/source/time budgets persisted
  in Settings.

## MT5 & live-trading safety (Phase 5 contract)
- Demo accounts first; `LIVE_TRADING` events + separate `approvals` table are required.
- Kill switch always available; process restart ⇒ strategy INACTIVE until
  explicitly re-armed.
- AI never places or modifies orders; execution is deterministic code.
- Every live order carries an approval id; missing approval ⇒ hard refusal.

## Reporting
Found a security issue? Do not open a public issue. Contact the repository owner
privately.
