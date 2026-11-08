"""
Python conversion of lms.sp_prepayment_quote (first attempt, before any parity run).

Pure function: no database. Takes the reference data and one case's inputs and returns the result.
Stand-in for what the Cursor agent produces. It is NOT expected to match every case.
"""
from datetime import date, timedelta
from decimal import Decimal as D, ROUND_HALF_UP

from dateutil.relativedelta import relativedelta


def r2(x):
    return D(x).quantize(D("0.01"))


def to_date(s):
    return s if isinstance(s, date) else date.fromisoformat(s)


def quote(reference, inputs):
    loan = inputs["loan"]
    qd = to_date(inputs["quote_date"])
    disb = to_date(loan["disb_date"])
    lid = loan["loan_id"]
    if loan["status"] != "ACTIVE":
        return {"error": f"loan {lid} is not active", "ledger": []}
    if qd < disb:
        return {"error": "quote date is before the disbursement date", "ledger": []}
    prod = next(p for p in reference["products"] if p["product_code"] == loan["product_code"])

    principal = D(loan["principal_outstanding"] if loan["principal_outstanding"] is not None
                  else loan["principal_disbursed"])
    if loan["rate_type"] == "FIXED":
        rate = D(loan["fixed_rate_pct"])
    else:
        rows = [b for b in reference["benchmark_rates"] if b["benchmark_code"] == loan["benchmark_code"]
                and to_date(b["effective_date"]) <= qd]
        bench = D(max(rows, key=lambda b: to_date(b["effective_date"]))["rate_pct"]) if rows else D(0)
        rate = bench

    start = to_date(loan["last_run_date"]) if loan["last_run_date"] else disb
    if prod["day_count"] == "30360":
        days = max((qd.year - start.year) * 360 + (qd.month - start.month) * 30 + (min(qd.day, 30) - min(start.day, 30)), 0)
    else:
        days = max((qd - start).days, 0)
    basis = D(365) if prod["day_count"] == "ACT365" else D(360)
    interest = D(loan["accrued_interest"]) + r2(principal * rate / 100 * days / basis)
    penal = D(loan["accrued_penal"])
    fees = D(loan["fees_outstanding"])

    prepay_fee = D(0)
    delta = relativedelta(qd, disb)
    left = int(loan["tenor_months"]) - (delta.years * 12 + delta.months)
    if loan["rate_type"] == "FIXED" and prod["prepay_fee_pct"] is not None and left > 3:
        prepay_fee = r2(principal * D(prod["prepay_fee_pct"]) / 100)

    gst = r2((prepay_fee + fees) * 18 / 100)
    total = principal + interest + penal + fees + prepay_fee + gst - D(loan["advance_balance"])
    result = {"o_principal": principal, "o_interest": interest, "o_penal": penal, "o_fees": fees,
              "o_prepay_fee": prepay_fee, "o_gst": gst, "o_total": total, "o_valid_until": qd + timedelta(days=2)}
    return {"result": result, "ledger": []}
