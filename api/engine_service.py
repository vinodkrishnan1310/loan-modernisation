"""Database adapters for the Python loan engines."""
from __future__ import annotations

from datetime import date

import psycopg

from service.prepayment_quote import quote

PRODUCT_SQL = "SELECT * FROM lms.loan_product"
BENCHMARK_SQL = "SELECT * FROM lms.benchmark_rate"
HOLIDAY_SQL = "SELECT * FROM lms.holiday"
LOAN_SQL = "SELECT * FROM lms.loan WHERE loan_id = %s"


def _load_reference(conn) -> dict:
    products = conn.execute(PRODUCT_SQL).fetchall()
    benchmarks = conn.execute(BENCHMARK_SQL).fetchall()
    holidays = conn.execute(HOLIDAY_SQL).fetchall()
    return {
        "products": [dict(row) for row in products],
        "benchmark_rates": [dict(row) for row in benchmarks],
        "holidays": [dict(row) for row in holidays],
    }


def run_prepayment_quote(conn, loan_id: int, quote_date: date) -> dict:
    row = conn.execute(LOAN_SQL, (loan_id,)).fetchone()
    if row is None:
        raise psycopg.errors.RaiseException(f"LMS-001 loan {loan_id} not found")

    out = quote(_load_reference(conn), {"loan": dict(row), "quote_date": quote_date})
    if "error" in out:
        raise psycopg.errors.RaiseException(out["error"])
    return out["result"]
