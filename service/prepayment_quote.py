"""Payoff quote for one loan (conversion of lms.sp_prepayment_quote). Pure function, no I/O."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal as D


def _r2(x: D) -> D:
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def _to_date(v: date | str) -> date:
    return v if isinstance(v, date) else date.fromisoformat(v)


def quote(reference: dict, inputs: dict) -> dict:
    """Return a payoff quote for one loan on quote_date."""
    loan = inputs["loan"]
    qd = _to_date(inputs["quote_date"])
    disb = _to_date(loan["disb_date"])
    lid = loan["loan_id"]
    if loan["status"] != "ACTIVE":
        return {"error": f"LMS-021 loan {lid} is {loan['status']} and cannot be quoted", "ledger": []}
    if qd < disb:
        return {"error": f"LMS-002 quote date {qd} is before disbursement {disb}", "ledger": []}

    prod = next(p for p in reference["products"] if p["product_code"] == loan["product_code"])

    principal = D(
        loan["principal_outstanding"] if loan["principal_outstanding"] is not None else loan["principal_disbursed"]
    )
    if loan["rate_type"] == "FIXED":
        rate = D(loan["fixed_rate_pct"])
    else:
        rows = [
            b
            for b in reference["benchmark_rates"]
            if b["benchmark_code"] == loan["benchmark_code"] and _to_date(b["effective_date"]) <= qd
        ]
        bench = D(max(rows, key=lambda b: _to_date(b["effective_date"]))["rate_pct"]) if rows else D(0)
        rate = bench + D(loan["spread_pct"] or 0)

    start = _to_date(loan["last_run_date"]) if loan["last_run_date"] else disb
    days = max((qd - start).days, 0)
    basis = D(365) if prod["day_count"] == "ACT365" else D(360)
    interest = D(loan["accrued_interest"]) + _r2(principal * rate / 100 * days / basis)
    penal = D(loan["accrued_penal"])
    fees = D(loan["fees_outstanding"])

    prepay_fee = D(0)
    months_in = (qd.year - disb.year) * 12 + (qd.month - disb.month)
    if qd.day < disb.day:
        months_in -= 1
    left = int(loan["tenor_months"]) - months_in
    if loan["rate_type"] == "FIXED" and prod["prepay_fee_pct"] is not None and left > 3:
        prepay_fee = _r2(principal * D(prod["prepay_fee_pct"]) / 100)

    gst = _r2((prepay_fee + fees) * 18 / 100)
    total = max(principal + interest + penal + fees + prepay_fee + gst - D(loan["advance_balance"]), D(0))
    result = {
        "o_principal": principal,
        "o_interest": interest,
        "o_penal": penal,
        "o_fees": fees,
        "o_prepay_fee": prepay_fee,
        "o_gst": gst,
        "o_total": total,
        "o_valid_until": qd + timedelta(days=2),
    }
    return {"result": result, "ledger": []}
