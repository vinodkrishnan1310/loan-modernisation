"""Database adapter for the converted prepayment quote.

Loads rows, calls the pure function, and writes ledger rows. The caller owns
the transaction. This module does not create or alter database objects.
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service.prepayment_quote import quote
from service.rate_reset_notice import notices

_LOAN_SQL = "SELECT * FROM lms.loan WHERE loan_id = %s"
_ACTIVE_FLOATING_SQL = """
    SELECT * FROM lms.loan
     WHERE status = 'ACTIVE' AND rate_type = 'FLOATING'
     ORDER BY loan_id
"""
_PRIOR_LEDGER_SQL = """
    SELECT loan_id, entry_date, entry_type, amount, note
      FROM lms.loan_ledger
     WHERE entry_type = 'RESET_NOTICE'
"""
_PRODUCT_SQL = """
    SELECT product_code, product_name, day_count, grace_days, penal_rate_pct,
           late_fee_flat, late_fee_pct, late_fee_cap, prepay_fee_pct, npa_dpd
      FROM lms.loan_product
"""
_BENCHMARK_SQL = """
    SELECT benchmark_code, effective_date, rate_pct
      FROM lms.benchmark_rate
"""
_HOLIDAY_SQL = "SELECT hdate, state_code, description FROM lms.holiday"
_LEDGER_SQL = """
    INSERT INTO lms.loan_ledger (loan_id, entry_date, entry_type, amount, note)
    VALUES (%s, %s, %s, %s, %s)
"""


def run_prepayment_quote(conn, loan_id, quote_date) -> dict:
    """Quote one loan and post the QUOTE ledger row.

    ``conn`` must return mapping rows, as the API connection does.
    Returns the legacy OUT keys. When the quote is refused, raises
    ``psycopg.errors.RaiseException`` with the procedure message.
    """
    loan = conn.execute(_LOAN_SQL, (loan_id,)).fetchone()
    reference = {
        "products": list(conn.execute(_PRODUCT_SQL).fetchall()),
        "benchmark_rates": list(conn.execute(_BENCHMARK_SQL).fetchall()),
        "holidays": list(conn.execute(_HOLIDAY_SQL).fetchall()),
    }
    outcome = quote(
        reference,
        {"loan": dict(loan) if loan is not None else None, "quote_date": quote_date, "loan_id": loan_id},
    )
    if "error" in outcome:
        raise psycopg.errors.RaiseException(outcome["error"])

    for row in outcome["ledger"]:
        conn.execute(
            _LEDGER_SQL,
            (
                int(row["loan_id"]),
                row["entry_date"],
                row["entry_type"],
                row["amount"],
                row["note"],
            ),
        )
    return outcome["result"]


def run_rate_reset_notice(conn, as_of, window_days) -> dict:
    """Run floating-rate reset notices over the active book.

    Loads loans and existing RESET_NOTICE rows, calls the pure function, posts
    new ledger rows, and returns ``p_posted`` and ``p_skipped``. Raises
    ``psycopg.errors.RaiseException`` when the procedure would refuse the run.
    """
    loans = [dict(row) for row in conn.execute(_ACTIVE_FLOATING_SQL).fetchall()]
    prior = [dict(row) for row in conn.execute(_PRIOR_LEDGER_SQL).fetchall()]
    reference = {
        "products": list(conn.execute(_PRODUCT_SQL).fetchall()),
        "benchmark_rates": list(conn.execute(_BENCHMARK_SQL).fetchall()),
        "holidays": list(conn.execute(_HOLIDAY_SQL).fetchall()),
    }
    outcome = notices(
        reference,
        {
            "loans": loans,
            "as_of": as_of,
            "window_days": window_days,
            "prior_ledger": prior,
        },
    )
    if "error" in outcome:
        raise psycopg.errors.RaiseException(outcome["error"])

    for row in outcome["ledger"]:
        conn.execute(
            _LEDGER_SQL,
            (
                int(row["loan_id"]),
                row["entry_date"],
                row["entry_type"],
                row["amount"],
                row["note"],
            ),
        )
    return {"p_posted": outcome["posted"], "p_skipped": outcome["skipped"]}
