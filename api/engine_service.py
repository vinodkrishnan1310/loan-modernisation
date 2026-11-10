"""Database adapters for the converted loan engines (ENGINE=service)."""
from __future__ import annotations

import pathlib
import sys
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service.prepayment_quote import quote as prepayment_quote  # noqa: E402

_LOAN_SQL = """
    SELECT loan_id, customer_ref, product_code, branch_state, disb_date,
           principal_disbursed, rate_type, fixed_rate_pct, benchmark_code, spread_pct,
           reset_months, tenor_months, emi, status, last_run_date, principal_outstanding,
           accrued_interest, accrued_penal, fees_outstanding, advance_balance, asset_class
      FROM lms.loan
     WHERE loan_id = %s
"""


def _serialize_loan(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for key, value in out.items():
        if isinstance(value, date):
            out[key] = value.isoformat()
        elif isinstance(value, Decimal):
            out[key] = str(value)
        elif value is not None and key in ("loan_id", "reset_months", "tenor_months"):
            out[key] = str(value)
    return out


def _load_reference(conn) -> dict:
    products = conn.execute(
        "SELECT product_code, product_name, day_count, grace_days, penal_rate_pct, "
        "late_fee_flat, late_fee_pct, late_fee_cap, prepay_fee_pct, npa_dpd "
        "FROM lms.loan_product"
    ).fetchall()
    benchmark_rates = conn.execute(
        "SELECT benchmark_code, effective_date, rate_pct FROM lms.benchmark_rate"
    ).fetchall()
    holidays = conn.execute(
        "SELECT calendar_date, description FROM lms.holiday"
    ).fetchall()
    for row in products:
        for key, value in list(row.items()):
            if isinstance(value, Decimal):
                row[key] = str(value)
    for row in benchmark_rates:
        if isinstance(row["effective_date"], date):
            row["effective_date"] = row["effective_date"].isoformat()
        if isinstance(row["rate_pct"], Decimal):
            row["rate_pct"] = str(row["rate_pct"])
    for row in holidays:
        for key in ("calendar_date", "hdate"):
            if key in row and isinstance(row[key], date):
                row[key] = row[key].isoformat()
    return {
        "products": products,
        "benchmark_rates": benchmark_rates,
        "holidays": holidays,
    }


def _raise_from_result(result: dict) -> None:
    error = result.get("error")
    if error:
        raise psycopg.errors.RaiseException(error)


def run_rate_reset_notice(conn, as_of: date, window_days: int) -> dict:
    """Converted in a separate PR; satisfies imports until then."""
    raise psycopg.errors.RaiseException(
        "service engine: rate reset notice is not built yet"
    )


def run_prepayment_quote(conn, loan_id: int, quote_date: date) -> dict:
    row = conn.execute(_LOAN_SQL, (loan_id,)).fetchone()
    if row is None:
        raise psycopg.errors.RaiseException(f"LMS-001 loan {loan_id} not found")
    reference = _load_reference(conn)
    result = prepayment_quote(
        reference,
        {"loan": _serialize_loan(row), "quote_date": quote_date.isoformat()},
    )
    _raise_from_result(result)
    return result["result"]
