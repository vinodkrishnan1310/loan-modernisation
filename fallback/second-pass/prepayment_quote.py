"""
Python conversion of lms.sp_prepayment_quote (second pass, after the first parity run).

Pure function: no database. Takes the reference data and one case's inputs and returns the
result and the ledger rows the procedure would write.
Stand-in for what the Cursor agent produces. It is NOT expected to match every case.
"""
from datetime import date, timedelta
from decimal import Decimal as D, ROUND_HALF_UP

from dateutil.relativedelta import relativedelta

GST_RATE = D(18)


def r2(x):
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def to_date(s):
    return s if isinstance(s, date) else date.fromisoformat(s)


class Calendar:
    def __init__(self, holidays):
        self.holidays = {(to_date(h["hdate"]), h["state_code"]) for h in holidays}

    def is_business_day(self, d, state):
        dow = (d.weekday() + 1) % 7                # 0 = Sunday
        if dow == 0:
            return False
        if dow == 6 and -(-d.day // 7) in (2, 4):   # second and fourth Saturday
            return False
        return (d, "ALL") not in self.holidays and (d, state) not in self.holidays

    def next_business_day(self, d, state):
        i = 0
        while not self.is_business_day(d, state) and i < 10:
            d += timedelta(days=1)
            i += 1
        return d


def rate_for_day(loan, day, benchmarks):
    if loan["rate_type"] == "FIXED":
        return D(loan["fixed_rate_pct"])
    disb = to_date(loan["disb_date"])
    delta = relativedelta(day, disb)
    months = delta.years * 12 + delta.months
    months = (months // int(loan["reset_months"])) * int(loan["reset_months"])
    reset_date = disb + relativedelta(months=months)
    rows = [b for b in benchmarks if b["benchmark_code"] == loan["benchmark_code"]
            and to_date(b["effective_date"]) <= reset_date]
    if not rows:
        return D(loan["fixed_rate_pct"])
    latest = max(rows, key=lambda b: to_date(b["effective_date"]))
    return D(latest["rate_pct"]) + D(loan["spread_pct"] or 0)


def quote(reference, inputs):
    loan = inputs["loan"]
    qd = to_date(inputs["quote_date"])
    disb = to_date(loan["disb_date"])
    lid = loan["loan_id"]
    if loan["status"] != "ACTIVE":
        return {"error": f"LMS-021 loan {lid} is {loan['status']} and cannot be quoted", "ledger": []}
    if qd < disb:
        return {"error": f"LMS-002 quote date {qd} is before disbursement {disb}", "ledger": []}
    prod = next(p for p in reference["products"] if p["product_code"] == loan["product_code"])
    cal = Calendar(reference["holidays"])

    principal = D(loan["principal_outstanding"] if loan["principal_outstanding"] is not None
                  else loan["principal_disbursed"])
    start = to_date(loan["last_run_date"]) if loan["last_run_date"] else disb
    days = max((qd - start).days, 0)
    basis = D(365) if prod["day_count"] == "ACT365" else D(360)
    rate = rate_for_day(loan, qd, reference["benchmark_rates"])
    interest = D(loan["accrued_interest"]) + r2(principal * rate / 100 * days / basis)

    penal = D(loan["accrued_penal"])
    fees = D(loan["fees_outstanding"])

    prepay_fee = D(0)
    if loan["rate_type"] == "FIXED" and prod["prepay_fee_pct"] is not None:
        delta = relativedelta(qd, disb)
        months_in = delta.years * 12 + delta.months
        if int(loan["tenor_months"]) - months_in > 3:
            prepay_fee = r2(principal * D(prod["prepay_fee_pct"]) / 100)

    gst = r2((prepay_fee + fees) * GST_RATE / 100)
    total = principal + interest + penal + fees + prepay_fee + gst - D(loan["advance_balance"])
    if total < 0:
        total = D(0)

    valid = cal.next_business_day(cal.next_business_day(qd, loan["branch_state"]) + timedelta(days=1),
                                  loan["branch_state"])
    result = {"o_principal": principal, "o_interest": interest, "o_penal": penal, "o_fees": fees,
              "o_prepay_fee": prepay_fee, "o_gst": gst, "o_total": total, "o_valid_until": valid}
    ledger = [{"loan_id": str(lid), "entry_date": qd.isoformat(), "entry_type": "QUOTE",
               "amount": r2(total), "note": f"valid until {valid}"}]
    return {"result": result, "ledger": ledger}
