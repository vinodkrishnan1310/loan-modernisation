#!/usr/bin/env bash
# Reset laptop + repo artefacts so you can run the module-2 Slack playbook again.
# Does NOT delete Docker volumes or git commits on main. Does NOT call GitHub.
#
#   bash deploy/reset_programme_demo.sh          # db loans + rules/evidence/state + legacy incoming
#   bash deploy/reset_programme_demo.sh --api    # also ENGINE=legacy and recreate api container
#
set -euo pipefail
cd "$(dirname "$0")/.."

reset_api=false
if [ "${1:-}" = "--api" ]; then
  reset_api=true
fi

echo "== demo loans 9001–9004 =="
bash deploy/reset_demo.sh

echo "== programme working files =="
rm -f rules/*.rules.json rules/*.rules.json.backup 2>/dev/null || true
rm -rf evidence/* 2>/dev/null || true
mkdir -p evidence rules
rm -f legacy/incoming/*.sql 2>/dev/null || true
mkdir -p legacy/incoming
touch legacy/incoming/.gitkeep
rm -f state/*.json 2>/dev/null || true
mkdir -p state

if $reset_api; then
  echo "== api ENGINE=legacy =="
  ENGINE=legacy docker compose up -d --build api
  curl -s localhost:8000/health || true
  echo
fi

cat <<'EOF'

Programme demo reset done.

Before the live Slack run:
  - Optional backup: cp rules/*.rules.json presenter/ before reset next time
  - Fresh thread in #loan-modernisation
  - Delete old mod/* branches on origin if replaying (branch = mod/<bundle basename without _bundle.sql, _ → -)

Replay order: see MODULE2_DEMO_STEPS.md and "Slack playbook (module 2)" in that file.
EOF
