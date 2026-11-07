"""Payoff quote for one loan.

Pure conversion of ``lms.sp_prepayment_quote``. No database, clock, or
environment. Money is ``Decimal``. Dates are ``datetime.date``.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext

GST_RATE = Decimal(18)
TWOPLACES = Decimal("0.01")
CALC_PRECISION = 50


def quote(reference: dict, inputs: dict) -> dict:
    """Quote a payoff.

    ``inputs`` is ``{"loan": {...}, "quote_date": "YYYY-MM-DD"}``. The loan
    dict uses the columns of ``lms.loan``. A missing loan is ``None``; pass
    ``loan_id`` in that case so the LMS-001 text can name it.

    Success returns ``{"result": {...}, "ledger": [row]}``. A refused quote
    returns ``{"error": "<first line>", "ledger": []}``.
    """
    loan = inputs.get("loan") if isinstance(inputs, dict) else None
    if loan is None:
        loan_id = inputs.get("loan_id") if isinstance(inputs, dict) else None
        return _error(f"LMS-001 loan {loan_id} not found")

    loan_id = loan.get("loan_id")
    status = loan.get("status")
    # NULL <> 'ACTIVE' is not true in Postgres, so a null status is not refused.
    if status is not None and status != "ACTIVE":
        return _error(f"LMS-021 loan {loan_id} is {status} and cannot be quoted")

    quote_date = _as_date(inputs.get("quote_date"))
    disb_date = _as_date(loan.get("disb_date"))
    if quote_date is not None and disb_date is not None and quote_date < disb_date:
        return _error(
            f"LMS-002 quote date {quote_date.isoformat()} is before disbursement {disb_date.isoformat()}"
        )

    product = _product(reference, loan.get("product_code"))
    if product is None:
        return _error(f"LMS-003 product {loan.get('product_code')} not found")

    principal = _dec(loan.get("principal_outstanding"))
    if principal is None:
        principal = _dec(loan.get("principal_disbursed"))

    accrued = _dec(loan.get("accrued_interest"))
    new_interest = _new_interest(loan, product, reference, principal, quote_date, disb_date)
    interest = _add(accrued, new_interest)
    penal = _dec(loan.get("accrued_penal"))
    fees = _dec(loan.get("fees_outstanding"))
    prepay_fee = _prepay_fee(loan, product, principal, quote_date, disb_date)
    gst = _gst(prepay_fee, fees)
    total = _payoff_total(principal, interest, penal, fees, prepay_fee, gst, loan.get("advance_balance"))

    holidays = _holiday_index(reference)
    valid_until = _valid_until(quote_date, loan.get("branch_state"), holidays)
    note = None if valid_until is None else f"valid until {valid_until.isoformat()}"
    entry_date = None if quote_date is None else quote_date.isoformat()

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
        "ledger": [
            {
                "loan_id": str(loan_id),
                "entry_date": entry_date,
                "entry_type": "QUOTE",
                "amount": total,
                "note": note,
            }
        ],
    }


def _error(message: str) -> dict:
    first = message.splitlines()[0] if message else message
    return {"error": first, "ledger": []}


def _product(reference: dict, code) -> dict | None:
    for product in reference.get("products") or []:
        if product.get("product_code") == code:
            return product
    return None


def _new_interest(loan, product, reference, principal, quote_date, disb_date):
    """Stored accrual is added by the caller. This is only the new simple interest."""
    v_from = _as_date(loan.get("last_run_date"))
    if v_from is None:
        v_from = disb_date
    # date - date is null when either side is null, and greatest(null, 0) is 0.
    if quote_date is None or v_from is None:
        days = 0
    else:
        days = max((quote_date - v_from).days, 0)
    basis = 365 if product.get("day_count") == "ACT365" else 360
    rate = _rate_for_day(loan, quote_date, reference.get("benchmark_rates") or [])
    return _round_ratio(principal, rate, days, basis)


def _prepay_fee(loan, product, principal, quote_date, disb_date):
    """Fixed-rate fee, waived unless more than three whole months of tenor remain."""
    fee = Decimal(0)
    pct = product.get("prepay_fee_pct")
    if loan.get("rate_type") != "FIXED" or pct is None:
        return fee
    tenor = _int(loan.get("tenor_months"))
    if tenor is None or quote_date is None or disb_date is None:
        return fee
    months_left = tenor - _whole_months(quote_date, disb_date)
    if months_left > 3:
        return _round_percent(principal, _dec(pct))
    return fee


def _gst(prepay_fee, fees):
    if prepay_fee is None or fees is None:
        return None
    return _round_percent(prepay_fee + fees, GST_RATE)


def _payoff_total(principal, interest, penal, fees, prepay_fee, gst, advance):
    advance_amount = _dec(advance)
    parts = (principal, interest, penal, fees, prepay_fee, gst, advance_amount)
    if any(part is None for part in parts):
        return None
    total = principal + interest + penal + fees + prepay_fee + gst - advance_amount
    if total < 0:
        return Decimal(0)
    return total


def _rate_for_day(loan, day, benchmarks):
    """Annual percent. FIXED uses fixed_rate_pct. Anything else steps back to a reset date."""
    if loan.get("rate_type") == "FIXED":
        return _dec(loan.get("fixed_rate_pct"))

    disb = _as_date(loan.get("disb_date"))
    reset_months = _int(loan.get("reset_months"))
    # A null day, null reset, or null disbursement cannot select a benchmark row.
    if day is None or disb is None or reset_months is None:
        return _dec(loan.get("fixed_rate_pct"))
    if reset_months == 0:
        raise ZeroDivisionError("division by zero")

    months = _whole_months(day, disb)
    stepped = _pg_int_div(months, reset_months) * reset_months
    reset = _add_months(disb, stepped)

    chosen = None
    chosen_on = None
    code = loan.get("benchmark_code")
    for row in benchmarks:
        if row.get("benchmark_code") != code:
            continue
        effective = _as_date(row.get("effective_date"))
        if effective is None or effective > reset:
            continue
        # ORDER BY effective_date DESC LIMIT 1. Equal dates keep the first row.
        if chosen_on is None or effective > chosen_on:
            chosen_on = effective
            chosen = row
    if chosen is None:
        return _dec(loan.get("fixed_rate_pct"))
    spread = _dec(loan.get("spread_pct"))
    if spread is None:
        spread = Decimal(0)
    return _dec(chosen.get("rate_pct")) + spread


def _valid_until(quote_date, state, holidays):
    """The business day after the first business day on or after the quote date."""
    if quote_date is None:
        return None
    first = _next_business_day(quote_date, state, holidays)
    if first is None:
        return None
    return _next_business_day(first + timedelta(days=1), state, holidays)


def _next_business_day(day, state, holidays):
    """First working day on or after ``day``. Stops after 10 steps."""
    if day is None:
        return None
    current = day
    steps = 0
    while not _is_business_day(current, state, holidays) and steps < 10:
        current = current + timedelta(days=1)
        steps += 1
    return current


def _is_business_day(day, state, holidays) -> bool:
    if day is None:
        return True
    dow = (day.weekday() + 1) % 7  # Postgres extract(dow): Sunday is 0, Saturday is 6.
    if dow == 0:
        return False
    if dow == 6:
        nth = math.ceil(day.day / 7)
        if nth in (2, 4):
            return False
    if (day, "ALL") in holidays or (day, state) in holidays:
        return False
    return True


def _holiday_index(reference) -> set:
    found = set()
    for row in reference.get("holidays") or []:
        day = _as_date(row.get("hdate"))
        code = row.get("state_code")
        if day is not None and code is not None:
            found.add((day, code))
    return found


def _whole_months(later: date, earlier: date) -> int:
    """``extract(year from age) * 12 + extract(month from age)``.

    The day of the month is not part of the count. One month is borrowed when
    the later day is before the earlier day, which is what ``age`` does for
    two real calendar dates.
    """
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    if later.day < earlier.day:
        months -= 1
    return months


def _add_months(start: date, months: int) -> date:
    """``date + (n || ' months')::interval``, clipped to the last day of the month."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return date(year, month, min(start.day, last_day))


def _pg_int_div(numerator: int, denominator: int) -> int:
    """Postgres integer division truncates toward zero."""
    if denominator == 0:
        raise ZeroDivisionError("division by zero")
    sign = 1 if (numerator >= 0) == (denominator >= 0) else -1
    return sign * (abs(numerator) // abs(denominator))


def _round_percent(amount, percent):
    """``round(amount * percent / 100, 2)``. Halves go away from zero."""
    if amount is None or percent is None:
        return None
    with localcontext() as ctx:
        ctx.prec = CALC_PRECISION
        ctx.rounding = ROUND_HALF_UP
        return (amount * percent / Decimal(100)).quantize(TWOPLACES)


def _round_ratio(principal, rate, days, basis):
    """``round(principal * rate / 100 * days / basis, 2)``."""
    if principal is None or rate is None:
        return None
    with localcontext() as ctx:
        ctx.prec = CALC_PRECISION
        ctx.rounding = ROUND_HALF_UP
        raw = principal * rate / Decimal(100) * Decimal(days) / Decimal(basis)
        return raw.quantize(TWOPLACES)


def _add(left, right):
    if left is None or right is None:
        return None
    return left + right


def _dec(value):
    if value is None or isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _int(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return int(str(value))


def _as_date(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    if not text:
        return None
    return date.fromisoformat(text[:10])
