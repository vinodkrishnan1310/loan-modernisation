"""
Record golden cases for the two extra stored procedures by running the REAL PL/pgSQL.

  lms.sp_prepayment_quote   -> golden/quote_cases.json
  lms.sp_rate_reset_notice  -> golden/notice_cases.json

Needs a running Postgres with sql/*.sql loaded. Env: PGHOST PGPORT PGUSER PGDATABASE.
Uses loan ids 8001 and up and removes them afterwards. Run from the repo root:
  python3 scripts/gen_cases_module2.py
"""
import json, os, pathlib
from datetime import date
import psycopg
from psycopg.rows import dict_row

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "golden"

conn = psycopg.connect(host=os.environ.get("PGHOST", "localhost"), port=int(os.environ.get("PGPORT", 5433)),
                       user=os.environ.get("PGUSER", "postgres"), dbname=os.environ.get("PGDATABASE", "loandb"),
                       autocommit=True, row_factory=dict_row)
cur = conn.cursor()

BASE = dict(loan_id=8001, customer_ref="GEN", product_code="HL-STD", branch_state="MH", disb_date="2024-01-10",
            principal_disbursed="1000000.00", rate_type="FIXED", fixed_rate_pct="9.0000", benchmark_code=None,
            spread_pct=None, reset_months=3, tenor_months=60, emi="1.00", status="ACTIVE", last_run_date=None,
            principal_outstanding="1000000.00", accrued_interest="0", accrued_penal="0", fees_outstanding="0",
            advance_balance="0", asset_class="STANDARD")
LOAN_COLS = list(BASE.keys())


def loan(**kw):
    d = dict(BASE); d.update(kw); return d


def floating(**kw):
    d = dict(rate_type="FLOATING", fixed_rate_pct=None, benchmark_code="REPO", spread_pct="2.5000"); d.update(kw)
    return loan(**d)


def hold_others():
    """sp_rate_reset_notice reads every loan in the database, so park the ones that are not ours."""
    cur.execute("UPDATE lms.loan SET status = 'HOLD:' || status WHERE (loan_id < 8000 OR loan_id >= 9000) AND status NOT LIKE 'HOLD:%'")


def release_others():
    cur.execute("UPDATE lms.loan SET status = substr(status, 6) WHERE status LIKE 'HOLD:%'")


def clean():
    cur.execute("DELETE FROM lms.loan_ledger WHERE loan_id >= 8000 AND loan_id < 9000")
    cur.execute("DELETE FROM lms.loan WHERE loan_id >= 8000 AND loan_id < 9000")


def put_loan(l):
    cols = ",".join(LOAN_COLS)
    ph = ",".join(f"%({c})s" for c in LOAN_COLS)
    cur.execute(f"INSERT INTO lms.loan ({cols}) VALUES ({ph})", l)


def s(v):
    if v is None: return None
    if isinstance(v, date): return v.isoformat()
    return str(v)


def ledger_rows(lo, hi):
    cur.execute("SELECT loan_id, entry_date, entry_type, amount, note FROM lms.loan_ledger "
                "WHERE loan_id BETWEEN %s AND %s ORDER BY ledger_id", (lo, hi))
    return [{k: s(v) for k, v in r.items()} for r in cur.fetchall()]


def err(e):
    return str(e).splitlines()[0]


hold_others()
# ------------------------------------------------------------------ quote cases
Q = [
 ("Q01", "fixed_mid_tenure",          loan(), "2024-06-15"),
 ("Q02", "fee_four_months_left",      loan(tenor_months=12), "2024-09-20"),
 ("Q03", "fee_waived_three_left",     loan(tenor_months=12), "2024-10-20"),
 ("Q04", "floating_no_fee",           floating(), "2024-11-12"),
 ("Q05", "act360_auto_loan",          loan(product_code="AL-ST", fixed_rate_pct="10.5000", tenor_months=36), "2024-05-03"),
 ("Q06", "thirty360_personal",        loan(product_code="PL-FLX", fixed_rate_pct="13.0000", principal_disbursed="500000.00", principal_outstanding="500000.00", branch_state="KA"), "2024-07-18"),
 ("Q07", "interest_since_last_run",   loan(last_run_date="2024-06-01", accrued_interest="1234.57", accrued_penal="310.40"), "2024-06-20"),
 ("Q08", "fees_outstanding_gst",      loan(fees_outstanding="576.25"), "2024-06-15"),
 ("Q09", "advance_exceeds_total",     loan(principal_outstanding="5000.00", principal_disbursed="5000.00", advance_balance="20000.00", tenor_months=12), "2024-02-15"),
 ("Q10", "quote_on_second_saturday",  loan(), "2024-06-08"),
 ("Q11", "validity_over_holiday",     loan(branch_state="DL"), "2024-03-22"),
 ("Q12", "fee_rounding_tie",          loan(principal_outstanding="1000000.25"), "2024-06-15"),
 ("Q13", "month_end_disbursal",       loan(disb_date="2024-01-31", tenor_months=6), "2024-04-30"),
 ("Q14", "month_end_disbursal_leap",  loan(disb_date="2024-02-29", tenor_months=6), "2024-05-29"),
 ("Q15", "msme_percent_fee",          loan(product_code="BL-MSM", fixed_rate_pct="11.2500", principal_outstanding="2500000.00", principal_disbursed="2500000.00", tenor_months=48), "2024-09-02"),
 ("Q16", "closed_loan_rejected",      loan(status="CLOSED"), "2024-06-15"),
 ("Q17", "date_before_disbursal",     loan(), "2023-12-31"),
 ("Q18", "no_outstanding_recorded",   loan(principal_outstanding=None), "2024-02-10"),
]
quote_cases = []
for cid, name, l, qd in Q:
    clean(); put_loan(l)
    rec = {"case_id": cid, "scenario": name, "inputs": {"loan": {k: s(v) for k, v in l.items()}, "quote_date": qd}}
    try:
        cur.execute("SELECT * FROM lms.sp_prepayment_quote(%s, %s::date)", (l["loan_id"], qd))
        res = {k: s(v) for k, v in cur.fetchone().items()}
        rec["legacy_output"] = {"result": res, "ledger": ledger_rows(8001, 8001)}
    except psycopg.errors.RaiseException as e:
        rec["legacy_output"] = {"error": err(e), "ledger": ledger_rows(8001, 8001)}
    quote_cases.append(rec)
clean()
json.dump(quote_cases, open(OUT / "quote_cases.json", "w"), indent=1)

# ------------------------------------------------------------------ notice cases
def fx(i, **kw):
    return loan(loan_id=8000 + i, **kw)

def fl(i, **kw):
    return floating(loan_id=8000 + i, **kw)

N = [
 ("N01", "only_fixed_loans",       [fx(1), fx(2)], "2024-09-20", 30, []),
 ("N02", "change_exactly_025",     [fl(1)], "2024-09-20", 30, []),
 ("N03", "change_too_small",       [fl(1)], "2024-03-01", 60, []),
 ("N04", "reset_outside_window",   [fl(1)], "2024-08-01", 30, []),
 ("N05", "notice_day_rolls",       [fl(1, disb_date="2024-07-13")], "2024-09-30", 30, []),
 ("N06", "benchmark_on_reset_day", [fl(1, disb_date="2024-07-09")], "2024-09-25", 30, []),
 ("N07", "npa_loan_skipped",       [fl(1, asset_class="NPA")], "2024-09-20", 30, []),
 ("N08", "already_noticed",        [fl(1)], "2024-09-20", 30,
        [{"loan_id": 8001, "entry_date": "2024-10-14", "entry_type": "RESET_NOTICE", "amount": "9.04", "note": "reset 2024-10-10 from 8.7900 to 9.0400"}]),
 ("N09", "window_too_large",       [fl(1)], "2024-09-20", 120, []),
 ("N10", "floating_without_bench", [fl(1, benchmark_code=None)], "2024-09-20", 30, []),
 ("N11", "mixed_book",             [fl(1), fl(2, disb_date="2024-07-09"), fl(3, asset_class="NPA"), fl(4, status="CLOSED"), fx(5)], "2024-09-20", 30, []),
 ("N12", "no_spread",              [fl(1, spread_pct=None, disb_date="2024-07-09")], "2024-09-25", 30, []),
 ("N13", "six_month_reset",        [fl(1, reset_months=6, disb_date="2024-04-09")], "2024-09-25", 30, []),
 ("N14", "month_end_30th",         [fl(1, disb_date="2024-07-31")], "2025-04-30", 10, []),
 ("N15", "month_end_leap",         [fl(1, disb_date="2024-02-29", reset_months=6)], "2025-08-28", 5, []),
 ("N16", "holiday_notice_day",     [fl(1, disb_date="2024-08-01", branch_state="KA")], "2024-10-20", 15, []),
]
notice_cases = []
for cid, name, loans, as_of, win, prior in N:
    clean()
    for l in loans: put_loan(l)
    for p in prior:
        cur.execute("INSERT INTO lms.loan_ledger (loan_id, entry_date, entry_type, amount, note) VALUES (%s,%s,%s,%s,%s)",
                    (p["loan_id"], p["entry_date"], p["entry_type"], p["amount"], p["note"]))
    before = len(ledger_rows(8001, 8999))
    rec = {"case_id": cid, "scenario": name,
           "inputs": {"loans": [{k: s(v) for k, v in l.items()} for l in loans], "as_of": as_of,
                      "window_days": win, "prior_ledger": prior}}
    try:
        cur.execute("CALL lms.sp_rate_reset_notice(%s::date, %s::int)", (as_of, win))
        row = cur.fetchone()
        rec["legacy_output"] = {"posted": row["p_posted"], "skipped": row["p_skipped"],
                                "ledger": ledger_rows(8001, 8999)[before:]}
    except psycopg.errors.RaiseException as e:
        rec["legacy_output"] = {"error": err(e), "ledger": ledger_rows(8001, 8999)[before:]}
    notice_cases.append(rec)
clean()
json.dump(notice_cases, open(OUT / "notice_cases.json", "w"), indent=1)

release_others()
for c in quote_cases:
    o = c["legacy_output"]
    print(c["case_id"], c["scenario"], o.get("error") or (o["result"]["o_total"], o["result"]["o_prepay_fee"], o["result"]["o_valid_until"]))
for c in notice_cases:
    o = c["legacy_output"]
    print(c["case_id"], c["scenario"], o.get("error") or (o["posted"], o["skipped"], [(r["loan_id"], r["entry_date"], r["amount"]) for r in o["ledger"]]))
