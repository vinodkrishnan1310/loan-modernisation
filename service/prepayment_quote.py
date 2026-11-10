"""Pure Python conversion of lms.sp_prepayment_quote (read-only payoff quote)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal as D

def _r2(x) -> D:
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def _to_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _product(reference: dict, product_code: str) -> dict:
    for product in reference["products"]:
        if product["product_code"] == product_code:
            return product
    raise KeyError(product_code)


def _rate_on_quote(reference: dict, loan: dict, quote_date: date) -> D:
    if loan["rate_type"] == "FIXED":
        return D(loan["fixed_rate_pct"])
    rows = [
        row
        for row in reference["benchmark_rates"]
        if row["benchmark_code"] == loan["benchmark_code"]
        and _to_date(row["effective_date"]) <= quote_date
    ]
    bench = (
        D(max(rows, key=lambda row: _to_date(row["effective_date"]))["rate_pct"])
        if rows
        else D(0)
    )
    return bench + D(loan["spread_pct"] or 0)


def _months_since_disb(quote_date: date, disb_date: date) -> int:
    """Matches extract(year/month from age(quote_date, disb_date)) in Postgres."""
    months = (quote_date.year - disb_date.year) * 12 + (quote_date.month - disb_date.month)
    if quote_date.day < disb_date.day:
        months -= 1
    return months


def quote(reference: dict, inputs: dict) -> dict:
    loan = inputs["loan"]
    quote_date = _to_date(inputs["quote_date"])
    disb_date = _to_date(loan["disb_date"])
    loan_id = loan["loan_id"]

    if loan["status"] != "ACTIVE":
        return {
            "error": f"LMS-021 loan {loan_id} is {loan['status']} and cannot be quoted",
            "ledger": [],
        }
    if quote_date < disb_date:
        return {
            "error": f"LMS-002 quote date {quote_date} is before disbursement {disb_date}",
            "ledger": [],
        }

    product = _product(reference, loan["product_code"])
    principal = D(
        loan["principal_outstanding"]
        if loan["principal_outstanding"] is not None
        else loan["principal_disbursed"]
    )
    rate = _rate_on_quote(reference, loan, quote_date)

    accrual_start = _to_date(loan["last_run_date"]) if loan["last_run_date"] else disb_date
    days = max((quote_date - accrual_start).days, 0)
    basis = D(365) if product["day_count"] == "ACT365" else D(360)
    interest = D(loan["accrued_interest"]) + _r2(principal * rate / D(100) * days / basis)
    penal = D(loan["accrued_penal"])
    fees = D(loan["fees_outstanding"])

    months_in = _months_since_disb(quote_date, disb_date)
    months_left = int(loan["tenor_months"]) - months_in
    prepay_fee = D(0)
    if (
        loan["rate_type"] == "FIXED"
        and product["prepay_fee_pct"] is not None
        and months_left > 3
    ):
        prepay_fee = _r2(principal * D(product["prepay_fee_pct"]) / D(100))

    gst = _r2((prepay_fee + fees) * D(18) / D(100))
    total = max(
        principal + interest + penal + fees + prepay_fee + gst - D(loan["advance_balance"]),
        D(0),
    )
    return {
        "result": {
            "o_principal": principal,
            "o_interest": interest,
            "o_penal": penal,
            "o_fees": fees,
            "o_prepay_fee": prepay_fee,
            "o_gst": gst,
            "o_total": total,
            "o_valid_until": quote_date + timedelta(days=2),
        },
        "ledger": [],
    }
