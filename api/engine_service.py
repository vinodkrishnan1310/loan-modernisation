"""Database adapters for ENGINE=service (module conversions)."""
from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg.errors

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service.prepayment_quote import quote

_LOAN_COLUMNS = (
    "loan_id",
    "customer_ref",
    "product_code",
    "branch_state",
    "disb_date",
    "principal_disbursed",
    "rate_type",
    "fixed_rate_pct",
    "benchmark_code",
    "spread_pct",
    "reset_months",
    "tenor_months",
    "emi",
    "status",
    "last_run_date",
    "principal_outstanding",
    "accrued_interest",
    "accrued_penal",
    "fees_outstanding",
    "advance_balance",
    "asset_class",
)


def _serialize(value):
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _loan_dict(row: dict) -> dict:
    return {col: _serialize(row[col]) for col in _LOAN_COLUMNS}


def _load_reference(conn) -> dict:
    products = conn.execute("SELECT * FROM lms.loan_product ORDER BY product_code").fetchall()
    benchmarks = conn.execute(
        "SELECT * FROM lms.benchmark_rate ORDER BY benchmark_code, effective_date"
    ).fetchall()
    return {
        "products": [{k: _serialize(v) for k, v in p.items()} for p in products],
        "benchmark_rates": [{k: _serialize(v) for k, v in b.items()} for b in benchmarks],
    }


def run_prepayment_quote(conn, loan_id: int, quote_date: date) -> dict:
    row = conn.execute(
        f"SELECT {', '.join(_LOAN_COLUMNS)} FROM lms.loan WHERE loan_id = %s",
        (loan_id,),
    ).fetchone()
    if row is None:
        raise psycopg.errors.RaiseException(f"LMS-001 loan {loan_id} not found")

    reference = _load_reference(conn)
    out = quote(
        reference,
        {"loan": _loan_dict(row), "quote_date": quote_date.isoformat()},
    )
    if "error" in out:
        raise psycopg.errors.RaiseException(out["error"])
    return out["result"]
