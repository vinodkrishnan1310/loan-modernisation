#!/usr/bin/env bash
# Re-creates demo loans 9001 and 9002 from sql/05_demo_loans.sql (idempotent).
# Use it before a rehearsal or a live run so the demo loan starts from a clean state.
# Needs the db container running:  docker compose up -d db
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -n "${PSQL_URL:-}" ]; then
  psql "$PSQL_URL" -q -v ON_ERROR_STOP=1 -f sql/05_demo_loans.sql
else
  docker compose exec -T db psql -U postgres -d loandb -q -v ON_ERROR_STOP=1 < sql/05_demo_loans.sql
fi
echo "demo loans 9001 and 9002 reset"
