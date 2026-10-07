#!/usr/bin/env bash
# =============================================================================
# LMSArena DATA provisioning (V4 data separation)
# =============================================================================
# Materialises the repository's authoritative DATA tree into LMSArena temporary
# storage so the application can run against it through the existing
# EVOLUTIONARY_LAB_DATA_ROOT mechanism (see backend/app/paths.py).
#
# Guarantees:
#   * DATA is never written into the coding workspace (clone staging happens in
#     temporary storage, outside the repository).
#   * The dataset is copied verbatim - no regeneration, filtering, renaming or
#     merging of records, genomes, backtests or lineage.
#   * No credential is stored in this file: if the repository is private, export
#     GITHUB_TOKEN (or GIT_ASKPASS) in the environment before running.
#
# Usage:
#   LMSARENA_DATA_ROOT=/opt/lmsarena-storage/DATA scripts/lmsarena_provision_data.sh
#
# After provisioning, point the application at the DATA tree:
#   export EVOLUTIONARY_LAB_DATA_ROOT=/opt/lmsarena-storage/DATA
#   cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8787
# =============================================================================
set -euo pipefail

REPO_URL="${LMSARENA_REPO_URL:-https://github.com/awais225/evolutionary-trading-lab.git}"
BRANCH="${LMSARENA_BRANCH:-main}"
TARGET="${LMSARENA_DATA_ROOT:-/opt/lmsarena-storage/DATA}"
STAGE="${LMSARENA_STAGE_DIR:-$(dirname "${TARGET}")/.provision}"

echo "[1/5] Preparing staging area in temporary storage: ${STAGE}"
mkdir -p "${STAGE}"
CLONE_DIR="${STAGE}/repo"

if [ -n "${GITHUB_TOKEN:-}" ]; then
  # Token is used transiently and never written to disk or to git config.
  ASKPASS="$(mktemp)"
  printf '#!/bin/sh\ncase "$1" in *[Uu]sername*) printf %%s "x-access-token" ;; *) printf %%s "${GITHUB_TOKEN}" ;; esac\n' > "${ASKPASS}"
  chmod 700 "${ASKPASS}"
  export GIT_ASKPASS="${ASKPASS}" GIT_TERMINAL_PROMPT=0
  trap 'rm -f "${ASKPASS}"' EXIT
fi

echo "[2/5] Fetching repository (branch: ${BRANCH})"
if [ -d "${CLONE_DIR}/.git" ]; then
  git -C "${CLONE_DIR}" fetch --depth 1 origin "${BRANCH}"
  git -C "${CLONE_DIR}" checkout -f FETCH_HEAD
else
  rm -rf "${CLONE_DIR}"
  git clone --depth 1 --branch "${BRANCH}" "${REPO_URL}" "${CLONE_DIR}"
fi

if command -v git-lfs >/dev/null 2>&1; then
  echo "      Resolving Git LFS objects (parquet/db assets)"
  git -C "${CLONE_DIR}" lfs install --local >/dev/null 2>&1 || true
  git -C "${CLONE_DIR}" lfs pull || echo "      WARNING: git-lfs pull failed - DATA may contain pointers"
else
  echo "      WARNING: git-lfs not installed; *.parquet / *.db may remain pointer files"
fi

if [ ! -d "${CLONE_DIR}/DATA" ]; then
  echo "ERROR: ${CLONE_DIR}/DATA not found - refusing to continue." >&2
  exit 1
fi

echo "[3/5] Importing DATA verbatim -> ${TARGET}"
mkdir -p "${TARGET}"
cp -a "${CLONE_DIR}/DATA/." "${TARGET}/"

echo "[4/5] Verifying population with the application's own classification"
DB="${TARGET}/DATABASE/lab_state.db"
if command -v python3 >/dev/null 2>&1 && [ -f "${DB}" ]; then
  python3 - "${DB}" <<'PY'
import sqlite3, sys
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
rows = dict(c.execute("SELECT data_source, count(*) FROM strategies GROUP BY data_source"))
total = c.execute("SELECT count(*) FROM strategies").fetchone()[0]
print(f"      strategies total      : {total}")
print(f"      USER_RESEARCH nodes   : {rows.get('USER_RESEARCH', 0)}")
print(f"      LEGACY_TEST nodes     : {rows.get('LEGACY_TEST', 0)}")
print(f"      schema user_version   : {c.execute('PRAGMA user_version').fetchone()[0]}")
PY
fi

echo "[5/5] Done. DATA tree: ${TARGET} ($(find "${TARGET}" -type f | wc -l) files)"
echo "      Remember: export EVOLUTIONARY_LAB_DATA_ROOT=${TARGET}"
