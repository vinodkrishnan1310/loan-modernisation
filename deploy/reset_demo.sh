#!/usr/bin/env bash
# Re-creates demo loans 9001 to 9004 from sql/05_demo_loans.sql and sql/08_demo_loans_module2.sql (idempotent).
# Use it before a rehearsal or a live run so the demo loan starts from a clean state.
# Needs the db container running:  docker compose up -d db
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -n "${PSQL_URL:-}" ]; then
  for f in sql/05_demo_loans.sql sql/08_demo_loans_module2.sql; do psql "$PSQL_URL" -q -v ON_ERROR_STOP=1 -f "$f"; done
else
  for f in sql/05_demo_loans.sql sql/08_demo_loans_module2.sql; do
    docker compose exec -T db psql -U postgres -d loandb -q -v ON_ERROR_STOP=1 < "$f"
  done
fi
echo "demo loans 9001 to 9004 reset"
