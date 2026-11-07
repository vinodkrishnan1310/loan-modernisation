"""
Python conversion of lms.sp_rate_reset_notice (second pass, after the first parity run).

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


class Calendar:
    def __init__(self, holidays):
        self.holidays = {(to_date(h["hdate"]), h["state_code"]) for h in holidays}

    def is_business_day(self, d, state):
        dow = (d.weekday() + 1) % 7
        if dow == 0:
            return False
        if dow == 6 and -(-d.day // 7) in (2, 4):
            return False
        return (d, "ALL") not in self.holidays and (d, state) not in self.holidays

    def next_business_day(self, d, state):
        i = 0
        while not self.is_business_day(d, state) and i < 10:
            d += timedelta(days=1)
            i += 1
        return d


def benchmark_on(benchmarks, code, day):
    rows = [b for b in benchmarks if b["benchmark_code"] == code and to_date(b["effective_date"]) <= day]
    return D(max(rows, key=lambda b: to_date(b["effective_date"]))["rate_pct"]) if rows else None


def rate_for_day(loan, day, benchmarks):
    disb = to_date(loan["disb_date"])
    delta = relativedelta(day, disb)
    months = delta.years * 12 + delta.months
    months = (months // int(loan["reset_months"])) * int(loan["reset_months"])
    bench = benchmark_on(benchmarks, loan["benchmark_code"], disb + relativedelta(months=months))
    if bench is None:
        return D(loan["fixed_rate_pct"])
    return bench + D(loan["spread_pct"] or 0)


def notices(reference, inputs):
    as_of = to_date(inputs["as_of"])
    window = inputs["window_days"]
    if window is None or window < 1 or window > 90:
        return {"error": f"LMS-031 window {window} days is outside 1 to 90", "ledger": []}
    cal = Calendar(reference["holidays"])
    seen = [(str(p["loan_id"]), p["entry_type"], p["note"]) for p in inputs.get("prior_ledger", [])]
    posted = skipped = 0
    ledger = []
    loans = sorted((l for l in inputs["loans"] if l["status"] == "ACTIVE" and l["rate_type"] == "FLOATING"),
                   key=lambda l: int(l["loan_id"]))
    for l in loans:
        if l["asset_class"] == "NPA":
            skipped += 1
            continue
        if l["benchmark_code"] is None:
            return {"error": f"LMS-005 floating loan {l['loan_id']} has no benchmark", "ledger": ledger}
        disb = to_date(l["disb_date"])
        delta = relativedelta(as_of, disb)
        months = delta.years * 12 + delta.months
        rm = int(l["reset_months"])
        months = (months // rm) * rm + rm
        next_reset = disb + relativedelta(months=months)
        if next_reset > as_of + timedelta(days=window):
            continue
        notice_day = cal.next_business_day(next_reset, l["branch_state"])
        bench = benchmark_on(reference["benchmark_rates"], l["benchmark_code"], next_reset)
        new_rate = (bench if bench is not None else D(0)) + D(l["spread_pct"] or 0)
        old_rate = rate_for_day(l, as_of, reference["benchmark_rates"])
        if abs(new_rate - old_rate) < MIN_CHANGE:
            skipped += 1
            continue
        prefix = f"reset {next_reset}"
        if any(s[0] == str(l["loan_id"]) and s[1] == "RESET_NOTICE" and s[2].startswith(prefix) for s in seen):
            skipped += 1
            continue
        note = f"reset {next_reset} from {old_rate} to {new_rate}"
        row = {"loan_id": str(l["loan_id"]), "entry_date": notice_day.isoformat(),
               "entry_type": "RESET_NOTICE", "amount": r2(new_rate), "note": note}
        ledger.append(row)
        seen.append((row["loan_id"], "RESET_NOTICE", note))
        posted += 1
    return {"posted": posted, "skipped": skipped, "ledger": ledger}
