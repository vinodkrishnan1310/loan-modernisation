"""
Python conversion of lms.sp_rate_reset_notice (first attempt, before any parity run).

Pure function: takes the reference data and one case's inputs (the whole loan book, the as-of
date, the window and any earlier ledger rows) and returns posted, skipped and the new ledger rows.
Stand-in for what the Cursor agent produces. It is NOT expected to match every case.
"""
from datetime import date, timedelta
from decimal import Decimal as D, ROUND_HALF_UP

from dateutil.relativedelta import relativedelta

MIN_CHANGE = D("0.25")


def r2(x):
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def to_date(s):
    return s if isinstance(s, date) else date.fromisoformat(s)


def _months_since_disb(as_of: date, disb: date) -> int:
    """``extract(year from age) * 12 + extract(month from age)`` (Postgres)."""
    months = (as_of.year - disb.year) * 12 + (as_of.month - disb.month)
    if as_of.day < disb.day:
        months -= 1
    return months


def benchmark_on(benchmarks, code, day):
    rows = [b for b in benchmarks if b["benchmark_code"] == code and to_date(b["effective_date"]) <= day]
    return D(max(rows, key=lambda b: to_date(b["effective_date"]))["rate_pct"]) if rows else D(0)


def notices(reference, inputs):
    as_of = to_date(inputs["as_of"])
    window = inputs["window_days"]
    if window is None or window < 1 or window > 90:
        return {"error": "window_days must be between 1 and 90", "ledger": []}
    bench = reference["benchmark_rates"]
    seen = [(str(p["loan_id"]), p["entry_type"], p["note"]) for p in inputs.get("prior_ledger", [])]
    posted = skipped = 0
    ledger = []
    loans = sorted((l for l in inputs["loans"] if l["status"] == "ACTIVE" and l["rate_type"] == "FLOATING"),
                   key=lambda l: int(l["loan_id"]))
    for l in loans:
        if l["benchmark_code"] is None:
            continue
        disb = to_date(l["disb_date"])
        rm = int(l["reset_months"])
        months_in = _months_since_disb(as_of, disb)
        months = (months_in // rm) * rm + rm
        next_reset = disb + relativedelta(months=months)
        if next_reset > as_of + timedelta(days=window):
            continue
        notice_day = next_reset
        spread = D(l["spread_pct"] or 0)
        new_rate = benchmark_on(bench, l["benchmark_code"], next_reset) + spread
        old_rate = benchmark_on(bench, l["benchmark_code"], disb + relativedelta(months=months - rm)) + spread
        prefix = f"reset {next_reset}"
        if abs(new_rate - old_rate) <= MIN_CHANGE:
            skipped += 1
            continue
        note = f"reset {next_reset} from {old_rate} to {new_rate}"
        row = {"loan_id": str(l["loan_id"]), "entry_date": notice_day.isoformat(),
               "entry_type": "RESET_NOTICE", "amount": r2(new_rate), "note": note}
        ledger.append(row)
        seen.append((row["loan_id"], "RESET_NOTICE", note))
        posted += 1
    return {"posted": posted, "skipped": skipped, "ledger": ledger}
