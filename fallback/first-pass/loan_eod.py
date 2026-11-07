"""
First-pass Python conversion of lms.sp_loan_eod_process.

Written as a straightforward translation of the procedure's visible logic, with
Decimal for money and no database. This is the stand-in for what the converter
bot / Cursor agent would produce on its first attempt. It is NOT expected to
match the legacy outputs exactly; the parity run shows where it does not.
"""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal as D

from dateutil.relativedelta import relativedelta

CENT = D("0.01")
GST_RATE = D(18)


def r2(x):
    """Round to 2 decimal places."""
    return D(x).quantize(CENT)


def to_date(s):
    return s if isinstance(s, date) else date.fromisoformat(s)


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------
class Calendar:
    def __init__(self, holidays):
        self.holidays = {(to_date(h["hdate"]), h["state_code"]) for h in holidays}

    def is_business_day(self, d, state):
        dow = (d.weekday() + 1) % 7            # 0 = Sunday, as in Postgres
        if dow == 0:
            return False
        if dow == 6:
            nth = -(-d.day // 7)               # ceil(day / 7)
            if nth in (2, 4):
                return False
        if (d, "ALL") in self.holidays or (d, state) in self.holidays:
            return False
        return True

    def next_business_day(self, d, state):
        i = 0
        while not self.is_business_day(d, state) and i < 10:
            d += timedelta(days=1)
            i += 1
        return d


# --------------------------------------------------------------------------
# Rates
# --------------------------------------------------------------------------
def rate_for_day(loan, day, benchmarks):
    if loan["rate_type"] == "FIXED":
        return D(loan["fixed_rate_pct"])

    disb = to_date(loan["disb_date"])
    delta = relativedelta(day, disb)
    months = delta.years * 12 + delta.months
    months = (months // loan["reset_months"]) * loan["reset_months"]
    reset_date = disb + relativedelta(months=months)

    rows = [b for b in benchmarks
            if b["benchmark_code"] == loan["benchmark_code"]
            and to_date(b["effective_date"]) <= reset_date]
    if not rows:
        return D(loan["fixed_rate_pct"]) if loan.get("fixed_rate_pct") is not None else None
    latest = max(rows, key=lambda b: to_date(b["effective_date"]))
    spread = D(loan["spread_pct"]) if loan.get("spread_pct") is not None else D(0)
    return D(latest["rate_pct"]) + spread


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def process_loan(reference, case_inputs):
    products = {p["product_code"]: p for p in reference["products"]}
    benchmarks = reference["benchmark_rates"]
    cal = Calendar(reference["holidays"])

    loan = deepcopy(case_inputs["loan"])
    prod = products[loan["product_code"]]
    p_as_of = to_date(case_inputs["as_of"])
    disb = to_date(loan["disb_date"])
    state = loan["branch_state"]

    insts = []
    for r in case_inputs["installments"]:
        insts.append({"inst_no": r["inst_no"], "due_date": to_date(r["due_date"]),
                      "principal_due": D(r["principal_due"]), "interest_due": None,
                      "paid_principal": D(0), "paid_interest": D(0), "late_fee_charged": False})
    pays = sorted(({"payment_id": p["payment_id"], "pay_date": to_date(p["pay_date"]),
                    "amount": D(p["amount"]), "pay_type": p["pay_type"]} for p in case_inputs["payments"]),
                  key=lambda p: p["payment_id"])

    s = {"principal_outstanding": D(loan["principal_disbursed"]), "accrued_interest": D(0),
         "accrued_penal": D(0), "suspense_interest": D(0), "fees_outstanding": D(0),
         "advance_balance": D(0), "asset_class": "STANDARD", "dpd": 0, "status": "ACTIVE"}
    ledger = []
    prev_rate = None
    sum_int = sum_penal = sum_fees = sum_paid = D(0)
    days = 0

    def post(d, typ, amount, note):
        ledger.append({"entry_date": d, "entry_type": typ, "amount": D(amount), "note": note})

    def apply_to_installments(amount):
        rest, int_paid, prin_paid = D(amount), D(0), D(0)
        for r in sorted(insts, key=lambda r: r["inst_no"]):
            if r["interest_due"] is None:
                continue
            if not ((r["interest_due"] - r["paid_interest"]) > 0
                    or (r["principal_due"] - r["paid_principal"]) > 0):
                continue
            if rest <= 0:
                break
            a = min(rest, r["interest_due"] - r["paid_interest"])
            if a > 0:
                r["paid_interest"] += a
                rest -= a
                int_paid += a
            a = min(rest, r["principal_due"] - r["paid_principal"])
            if a > 0:
                r["paid_principal"] += a
                rest -= a
                prin_paid += a
        return rest, int_paid, prin_paid

    day = disb
    while day < p_as_of:
        day += timedelta(days=1)
        days += 1

        # rate
        rate = rate_for_day(loan, day, benchmarks)
        if rate is None or rate < 0 or rate > 60:
            raise ValueError(f"LMS-004 bad rate {rate} on {day}")
        if prev_rate != rate:
            if prev_rate is not None:
                post(day, "RATE_RESET", rate, f"from {prev_rate:.4f} to {rate:.4f}")
            prev_rate = rate

        # billing
        for r in sorted((r for r in insts if r["due_date"] == day and r["interest_due"] is None),
                        key=lambda r: r["inst_no"]):
            r["interest_due"] = s["accrued_interest"]
            post(day, "BILL_INTEREST", s["accrued_interest"], f"inst {r['inst_no']}")
            s["accrued_interest"] = D(0)
            if s["advance_balance"] > 0:
                rest, _, prin_paid = apply_to_installments(s["advance_balance"])
                s["principal_outstanding"] -= prin_paid
                post(day, "ADVANCE_APPLIED", s["advance_balance"] - rest, f"inst {r['inst_no']}")
                s["advance_balance"] = rest

        # payments
        for p in (p for p in pays if p["pay_date"] == day):
            rem = p["amount"]
            sum_paid += p["amount"]

            alloc = min(rem, s["fees_outstanding"])
            if alloc > 0:
                s["fees_outstanding"] -= alloc
                rem -= alloc
                post(day, "PAY_FEES", alloc, f"payment {p['payment_id']}")

            alloc = min(rem, s["accrued_penal"])
            if alloc > 0:
                s["accrued_penal"] -= alloc
                rem -= alloc
                post(day, "PAY_PENAL", alloc, f"payment {p['payment_id']}")

            rem, int_paid, prin_paid = apply_to_installments(rem)
            if int_paid > 0:
                post(day, "PAY_INTEREST", int_paid, f"payment {p['payment_id']}")
            if prin_paid > 0:
                s["principal_outstanding"] -= prin_paid
                post(day, "PAY_PRINCIPAL", prin_paid, f"payment {p['payment_id']}")

            if rem > 0:
                if p["pay_type"] == "PREPAYMENT":
                    prepaid = min(rem, s["principal_outstanding"])
                    if prepaid > 0:
                        if loan["rate_type"] == "FIXED" and prod["prepay_fee_pct"] is not None:
                            fee = r2(prepaid * D(prod["prepay_fee_pct"]) / 100)
                            gst = r2(fee * GST_RATE / 100)
                            s["fees_outstanding"] += fee + gst
                            sum_fees += fee + gst
                            post(day, "PREPAY_FEE", fee, f"on {prepaid:.2f}")
                            post(day, "GST_ON_FEE", gst, "prepay fee")
                        s["principal_outstanding"] -= prepaid
                        rem -= prepaid
                        post(day, "PREPAYMENT", prepaid, f"payment {p['payment_id']}")

                        trim = prepaid
                        for r in sorted((r for r in insts if r["interest_due"] is None),
                                        key=lambda r: r["inst_no"], reverse=True):
                            if trim <= 0:
                                break
                            cut = min(trim, r["principal_due"])
                            if cut >= r["principal_due"]:
                                insts.remove(r)
                            else:
                                r["principal_due"] -= cut
                            trim -= cut
                if rem > 0:
                    s["advance_balance"] += rem
                    post(day, "ADVANCE_CREDIT", rem, f"payment {p['payment_id']}")

        # accrual
        if s["principal_outstanding"] > 0:
            factor = 1
            if prod["day_count"] == "30360":
                if day.day == 31:
                    factor = 0
                elif day.month == 2 and (day + timedelta(days=1)).month == 3:
                    factor = 30 - day.day + 1
            base = s["principal_outstanding"] * rate / 100
            if prod["day_count"] == "ACT365":
                acc = base / 365
            elif prod["day_count"] == "ACT360":
                acc = base / 360
            else:
                acc = base / 360 * factor
            acc_post = r2(acc)
            if s["asset_class"] == "NPA":
                s["suspense_interest"] += acc_post
            else:
                s["accrued_interest"] += acc_post
            sum_int += acc_post

        # overdue scan
        for r in sorted(insts, key=lambda r: r["inst_no"]):
            if r["interest_due"] is None:
                continue
            owed = (r["principal_due"] - r["paid_principal"]) + (r["interest_due"] - r["paid_interest"])
            if owed <= 0:
                continue
            eff_due = cal.next_business_day(r["due_date"], state)
            if day > eff_due + timedelta(days=prod["grace_days"] or 0):
                overdue = owed
                if not r["late_fee_charged"]:
                    parts = []
                    if prod["late_fee_flat"] is not None:
                        parts.append(D(prod["late_fee_flat"]))
                    if prod["late_fee_pct"] is not None:
                        parts.append(r2(overdue * D(prod["late_fee_pct"]) / 100))
                    fee = max(parts) if parts else D(0)
                    if prod["late_fee_cap"] is not None:
                        fee = min(fee, D(prod["late_fee_cap"]))
                    if fee > 0:
                        gst = r2(fee * GST_RATE / 100)
                        s["fees_outstanding"] += fee + gst
                        sum_fees += fee + gst
                        post(day, "LATE_FEE", fee, f"inst {r['inst_no']}")
                        post(day, "GST_ON_FEE", gst, f"late fee inst {r['inst_no']}")
                    r["late_fee_charged"] = True
                if s["asset_class"] != "NPA":
                    penal = r2(overdue * D(prod["penal_rate_pct"]) / 100 / 365)
                    s["accrued_penal"] += penal
                    sum_penal += penal

        # dpd and class
        unpaid_dates = [r["due_date"] for r in insts
                        if r["interest_due"] is not None and r["due_date"] < day
                        and (r["principal_due"] - r["paid_principal"]) + (r["interest_due"] - r["paid_interest"]) > 0]
        dpd = (day - min(unpaid_dates)).days if unpaid_dates else 0
        npa_dpd = prod["npa_dpd"] if prod["npa_dpd"] is not None else 90
        if dpd == 0:
            cls = "STANDARD"
        elif dpd <= 30:
            cls = "SMA0"
        elif dpd <= 60:
            cls = "SMA1"
        elif dpd <= npa_dpd:
            cls = "SMA2"
        else:
            cls = "NPA"

        if cls == "NPA" and s["asset_class"] != "NPA":
            s["suspense_interest"] += s["accrued_interest"]
            post(day, "NPA_REVERSAL", s["accrued_interest"], "to suspense")
            s["accrued_interest"] = D(0)
        elif cls != "NPA" and s["asset_class"] == "NPA":
            s["accrued_interest"] += s["suspense_interest"]
            post(day, "NPA_UPGRADE", s["suspense_interest"], "from suspense")
            s["suspense_interest"] = D(0)

        if cls != s["asset_class"]:
            post(day, "CLASS_CHANGE", dpd, f"{s['asset_class']} to {cls}")
        s["asset_class"] = cls
        s["dpd"] = dpd

        # closure
        if s["principal_outstanding"] == 0:
            unpaid = [r for r in insts if r["interest_due"] is None
                      or (r["principal_due"] - r["paid_principal"]) + (r["interest_due"] - r["paid_interest"]) > 0]
            if not unpaid and s["fees_outstanding"] == 0 and s["accrued_penal"] == 0:
                if s["accrued_interest"] > 0:
                    post(day, "WRITEOFF_ACCRUED", s["accrued_interest"], "broken period interest")
                    s["accrued_interest"] = D(0)
                s["status"] = "CLOSED"
                post(day, "CLOSED", 0, "loan closed")
                break

    return {
        "loan": {"status": s["status"], "last_run_date": day, "current_rate_pct": prev_rate,
                 "principal_outstanding": s["principal_outstanding"], "accrued_interest": s["accrued_interest"],
                 "accrued_penal": s["accrued_penal"], "suspense_interest": s["suspense_interest"],
                 "fees_outstanding": s["fees_outstanding"], "advance_balance": s["advance_balance"],
                 "asset_class": s["asset_class"], "dpd": s["dpd"]},
        "installments": sorted(insts, key=lambda r: r["inst_no"]),
        "ledger": ledger,
        "summary": {"days_processed": days, "interest_accrued": sum_int, "penal_accrued": sum_penal,
                    "fees_charged": sum_fees, "payments_applied": sum_paid, "dpd": s["dpd"],
                    "asset_class": s["asset_class"]},
    }
