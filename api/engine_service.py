"""Database adapters for the converted Python service engine."""
from __future__ import annotations

import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

import psycopg.errors

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service.rate_reset_notice import notices  # noqa: E402


def _serialize(value):
    if value is None:
        return None
    if isinstance(value, (dt.date, dt.datetime)):
        return value.date().isoformat() if isinstance(value, dt.datetime) else value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _load_reference(conn):
    benchmarks = conn.execute(
        "SELECT benchmark_code, effective_date, rate_pct FROM lms.benchmark_rate "
        "ORDER BY benchmark_code, effective_date"
    ).fetchall()
    return {
        "benchmark_rates": [{k: _serialize(v) for k, v in r.items()} for r in benchmarks],
    }


def _load_loans(conn):
    rows = conn.execute(
        "SELECT * FROM lms.loan WHERE status = %s AND rate_type = %s ORDER BY loan_id",
        ("ACTIVE", "FLOATING"),
    ).fetchall()
    return [{k: _serialize(v) for k, v in r.items()} for r in rows]


def _load_prior_ledger(conn):
    rows = conn.execute(
        "SELECT loan_id, entry_date, entry_type, amount, note FROM lms.loan_ledger ORDER BY ledger_id"
    ).fetchall()
    return [{k: _serialize(v) for k, v in r.items()} for r in rows]


def run_rate_reset_notice(conn, as_of: dt.date, window_days: int) -> dict:
    reference = _load_reference(conn)
    inputs = {
        "loans": _load_loans(conn),
        "as_of": as_of.isoformat(),
        "window_days": window_days,
        "prior_ledger": _load_prior_ledger(conn),
    }
    result = notices(reference, inputs)
    if "error" in result:
        raise psycopg.errors.RaiseException(result["error"])
    for row in result.get("ledger", []):
        conn.execute(
            "INSERT INTO lms.loan_ledger (loan_id, entry_date, entry_type, amount, note) "
            "VALUES (%s, %s::date, %s, %s, %s)",
            (int(row["loan_id"]), row["entry_date"], row["entry_type"], row["amount"], row["note"]),
        )
    return {"p_posted": result["posted"], "p_skipped": result["skipped"]}
