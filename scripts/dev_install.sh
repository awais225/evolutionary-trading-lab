#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pip install -U pip
python3 -m pip install -e packages/storage -e packages/eventbus -e packages/ai_orchestrator \
  -e packages/strategy_spec -e packages/exec_semantics -e packages/indicators \
  -e packages/mt5_adapter -e packages/research -e services/api -r requirements-dev.txt
echo "Dev install complete. Run: python scripts/init_db.py --create && pytest"
