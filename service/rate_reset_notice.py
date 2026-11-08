"""Advance notice of floating-rate resets.

Pure conversion of ``lms.sp_rate_reset_notice``. No database, clock, or
environment. Money rates are ``Decimal``. Dates are ``datetime.date``.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import Decimal

MIN_CHANGE = Decimal("0.25")


def notices(reference: dict, inputs: dict) -> dict:
    """Post RESET_NOTICE rows for loans whose next reset is inside the window.

    ``inputs`` is ``{"loans": [...], "as_of": "YYYY-MM-DD", "window_days": int,
    "prior_ledger": [...]}``. Loans outside ACTIVE FLOATING are ignored.

    Success returns ``{"posted": int, "skipped": int, "ledger": [...]}``.
    Validation errors return ``{"error": str, "ledger": [...]}``; on LMS-005
    the ledger holds rows posted before the failing loan.
    """
    window = inputs.get("window_days") if isinstance(inputs, dict) else None
    if window is None or window < 1 or window > 90:
        return _error(f"LMS-031 window {window} days is outside 1 to 90", [])

    as_of = _as_date(inputs.get("as_of"))
    holidays = _holiday_index(reference)
    benchmarks = reference.get("benchmark_rates") or []
    prior = inputs.get("prior_ledger") or []

    posted = 0
    skipped = 0
    ledger: list[dict] = []
    seen = _existing_notices(prior)

    loans = sorted(
        (loan for loan in (inputs.get("loans") or []) if _is_eligible(loan)),
        key=lambda row: int(row["loan_id"]),
    )

    for loan in loans:
        if loan.get("asset_class") == "NPA":
            skipped += 1
            continue

        if loan.get("benchmark_code") is None:
            return _error(f"LMS-005 floating loan {loan.get('loan_id')} has no benchmark", ledger)

        disb_date = _as_date(loan.get("disb_date"))
        reset_months = _int(loan.get("reset_months"))
        if reset_months is None or disb_date is None or as_of is None:
            continue

        months = _whole_months(as_of, disb_date)
        stepped = _pg_int_div(months, reset_months) * reset_months + reset_months
        next_reset = _add_months(disb_date, stepped)

        if next_reset > as_of + timedelta(days=window):
            continue

        notice_day = _next_business_day(next_reset, loan.get("branch_state"), holidays)
        bench = _benchmark_on(benchmarks, loan.get("benchmark_code"), next_reset)
        spread = _dec(loan.get("spread_pct"))
        if spread is None:
            spread = Decimal(0)
        new_rate = (_dec(bench) if bench is not None else Decimal(0)) + spread
        old_rate = _rate_for_day(loan, as_of, benchmarks)

        if abs(new_rate - old_rate) < MIN_CHANGE:
            skipped += 1
            continue

        prefix = f"reset {next_reset}"
        loan_id = str(loan.get("loan_id"))
        if seen.get((loan_id, prefix)):
            skipped += 1
            continue

        note = f"reset {next_reset} from {old_rate} to {new_rate}"
        row = {
            "loan_id": loan_id,
            "entry_date": notice_day.isoformat() if notice_day is not None else None,
            "entry_type": "RESET_NOTICE",
            "amount": new_rate,
            "note": note,
        }
        ledger.append(row)
        seen[(loan_id, prefix)] = True
        posted += 1

    return {"posted": posted, "skipped": skipped, "ledger": ledger}


def _error(message: str, ledger: list) -> dict:
    first = message.splitlines()[0] if message else message
    return {"error": first, "ledger": ledger}


def _is_eligible(loan) -> bool:
    return loan.get("status") == "ACTIVE" and loan.get("rate_type") == "FLOATING"


def _existing_notices(prior_ledger) -> dict[tuple[str, str], bool]:
    found: dict[tuple[str, str], bool] = {}
    for row in prior_ledger:
        loan_id = str(row.get("loan_id"))
        if row.get("entry_type") != "RESET_NOTICE":
            continue
        note = row.get("note") or ""
        for prefix in _notice_prefixes(note):
            found[(loan_id, prefix)] = True
    return found


def _notice_prefixes(note: str) -> list[str]:
    """Match ``note LIKE 'reset ' || v_next_reset || '%'`` for stored rows."""
    if not note.startswith("reset "):
        return []
    rest = note[6:]
    if not rest:
        return []
    if " " in rest:
        reset_date = rest.split(" ", 1)[0]
    else:
        reset_date = rest
    return [f"reset {reset_date}"]


def _benchmark_on(benchmarks, code, day: date):
    chosen = None
    chosen_on = None
    for row in benchmarks:
        if row.get("benchmark_code") != code:
            continue
        effective = _as_date(row.get("effective_date"))
        if effective is None or effective > day:
            continue
        if chosen_on is None or effective > chosen_on:
            chosen_on = effective
            chosen = row
    if chosen is None:
        return None
    return chosen.get("rate_pct")


def _rate_for_day(loan, day: date | None, benchmarks):
    """``lms.fn_rate_for_day`` for one in-memory loan row."""
    if loan.get("rate_type") == "FIXED":
        return _dec(loan.get("fixed_rate_pct"))

    disb = _as_date(loan.get("disb_date"))
    reset_months = _int(loan.get("reset_months"))
    if day is None or disb is None or reset_months is None:
        return _dec(loan.get("fixed_rate_pct"))
    if reset_months == 0:
        raise ZeroDivisionError("division by zero")

    months = _whole_months(day, disb)
    stepped = _pg_int_div(months, reset_months) * reset_months
    reset = _add_months(disb, stepped)

    bench = _benchmark_on(benchmarks, loan.get("benchmark_code"), reset)
    if bench is None:
        return _dec(loan.get("fixed_rate_pct"))
    spread = _dec(loan.get("spread_pct"))
    if spread is None:
        spread = Decimal(0)
    return _dec(bench) + spread


def _next_business_day(day, state, holidays):
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
    dow = (day.weekday() + 1) % 7
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
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    if later.day < earlier.day:
        months -= 1
    return months


def _add_months(start: date, months: int) -> date:
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
    if denominator == 0:
        raise ZeroDivisionError("division by zero")
    sign = 1 if (numerator >= 0) == (denominator >= 0) else -1
    return sign * (abs(numerator) // abs(denominator))


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
