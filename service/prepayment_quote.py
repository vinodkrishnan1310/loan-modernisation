"""Payoff quote for one loan (read-only; matches simplified sql/06)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

GST_RATE = Decimal(18)
TWOPLACES = Decimal("0.01")


def quote(reference: dict, inputs: dict) -> dict:
    """Quote a payoff. Success: ``{"result": {...}, "ledger": []}``."""
    loan = inputs.get("loan") if isinstance(inputs, dict) else None
    if loan is None:
        loan_id = inputs.get("loan_id") if isinstance(inputs, dict) else None
        return _error(f"LMS-001 loan {loan_id} not found")

    loan_id = loan.get("loan_id")
    status = loan.get("status")
    if status is not None and status != "ACTIVE":
        return _error(f"LMS-021 loan {loan_id} is {status} and cannot be quoted")

    qd = _as_date(inputs.get("quote_date"))
    disb = _as_date(loan.get("disb_date"))
    if qd is not None and disb is not None and qd < disb:
        return _error(f"LMS-002 quote date {qd} is before disbursement {disb}")

    products = reference.get("products") or []
    prod = next((p for p in products if p.get("product_code") == loan.get("product_code")), None)
    if prod is None:
        return _error(f"LMS-003 product {loan.get('product_code')} not found")

    principal = _dec(loan.get("principal_outstanding"))
    if principal is None:
        principal = _dec(loan.get("principal_disbursed"))

    rate = _rate(loan, qd, reference.get("benchmark_rates") or [])
    start = _as_date(loan.get("last_run_date")) or disb
    days = max((qd - start).days, 0) if qd is not None and start is not None else 0
    basis = Decimal(365) if prod.get("day_count") == "ACT365" else Decimal(360)
    accrued = _dec(loan.get("accrued_interest"))
    new_int = _round_ratio(principal, rate, days, basis)
    interest = accrued + new_int if accrued is not None and new_int is not None else None

    penal = _dec(loan.get("accrued_penal"))
    fees = _dec(loan.get("fees_outstanding"))
    prepay_fee = _prepay_fee(loan, prod, principal, qd, disb)
    gst = _round_percent(prepay_fee + fees, GST_RATE) if prepay_fee is not None and fees is not None else None
    advance = _dec(loan.get("advance_balance"))
    total = _total(principal, interest, penal, fees, prepay_fee, gst, advance)
    valid_until = qd + timedelta(days=2) if qd is not None else None

    return {
        "result": {
            "o_principal": principal,
            "o_interest": interest,
            "o_penal": penal,
            "o_fees": fees,
            "o_prepay_fee": prepay_fee,
            "o_gst": gst,
            "o_total": total,
            "o_valid_until": valid_until,
        },
        "ledger": [],
    }


def _error(message: str) -> dict:
    return {"error": message.splitlines()[0], "ledger": []}


def _rate(loan, qd, benchmarks):
    if loan.get("rate_type") == "FIXED":
        return _dec(loan.get("fixed_rate_pct"))
    if qd is None:
        return None
    code = loan.get("benchmark_code")
    rows = [
        b
        for b in benchmarks
        if b.get("benchmark_code") == code and _as_date(b.get("effective_date")) <= qd
    ]
    bench = _dec(max(rows, key=lambda b: _as_date(b["effective_date"]))["rate_pct"]) if rows else Decimal(0)
    spread = _dec(loan.get("spread_pct")) or Decimal(0)
    return bench + spread


def _prepay_fee(loan, prod, principal, qd, disb):
    fee = Decimal(0)
    if loan.get("rate_type") != "FIXED" or prod.get("prepay_fee_pct") is None:
        return fee
    if qd is None or disb is None:
        return fee
    tenor = loan.get("tenor_months")
    if tenor is None:
        return fee
    left = int(tenor) - _age_months(qd, disb)
    if left > 3 and principal is not None:
        return _round_percent(principal, _dec(prod["prepay_fee_pct"]))
    return fee


def _total(principal, interest, penal, fees, prepay_fee, gst, advance):
    parts = (principal, interest, penal, fees, prepay_fee, gst, advance)
    if any(p is None for p in parts):
        return None
    total = principal + interest + penal + fees + prepay_fee + gst - advance
    return max(total, Decimal(0))


def _round_percent(amount, percent):
    if amount is None or percent is None:
        return None
    return (amount * percent / Decimal(100)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _round_ratio(principal, rate, days, basis):
    if principal is None or rate is None:
        return None
    raw = principal * rate / Decimal(100) * Decimal(days) / basis
    return raw.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _dec(value):
    if value is None or isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _age_months(later: date, earlier: date) -> int:
    """``extract(year from age) * 12 + extract(month from age)``."""
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    if later.day < earlier.day:
        months -= 1
    return months


def _as_date(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    return date.fromisoformat(text[:10]) if text else None
