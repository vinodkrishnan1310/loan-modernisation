#!/usr/bin/env bash
# Applies module-2 legacy procedures to an existing database volume.
# sql/06 and sql/07 are not in docker init order on volumes created before module 2.
# Needs: docker compose up -d db
set -euo pipefail
cd "$(dirname "$0")/.."
apply() {
  local f="$1"
  if [ -n "${PSQL_URL:-}" ]; then
    psql "$PSQL_URL" -q -v ON_ERROR_STOP=1 -f "$f"
  else
    docker compose exec -T db psql -U postgres -d loandb -q -v ON_ERROR_STOP=1 < "$f"
  fi
}
apply sql/06_sp_prepayment_quote.sql
apply sql/07_sp_rate_reset_notice.sql
echo "module 2 procedures installed (sp_prepayment_quote, sp_rate_reset_notice)"
