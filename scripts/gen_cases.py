"""
Generate loan cases, run the REAL PL/pgSQL procedure, and save a golden master.

Pass 1 (adaptive): run the procedure installment by installment, deciding payment
amounts from the bills the legacy code actually produced.
Pass 2 (canonical): recreate the same loan with ALL payments preloaded and call the
procedure once. The golden master is recorded from pass 2, and pass 2 must equal
pass 1 (checked), so the run is not sensitive to how the batch was scheduled.

Needs a running Postgres with sql/*.sql loaded. Env: PGHOST PGPORT PGUSER PGDATABASE.
"""
import json, os, random, sys, pathlib
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from dateutil.relativedelta import relativedelta
import psycopg
from psycopg.rows import dict_row

HERE = pathlib.Path(__file__).parent
OUT = HERE.parent / "golden"
OUT.mkdir(exist_ok=True)

D = Decimal
def q2(x): return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)

conn = psycopg.connect(
    host=os.environ.get("PGHOST", "localhost"), port=int(os.environ.get("PGPORT", 5433)),
    user=os.environ.get("PGUSER", "postgres"), dbname=os.environ.get("PGDATABASE", "loandb"),
    autocommit=True, row_factory=dict_row)
cur = conn.cursor()

for t in ("loan_ledger","loan_run_summary","loan_error_log","payment","installment","loan"):
    cur.execute(f"DELETE FROM lms.{t}")

_pay_seq = [100000]
def next_pay_id():
    _pay_seq[0] += 1
    return _pay_seq[0]

# ------------------------------------------------------------------ schedule
def make_schedule(principal, rate_pct, tenor, first_due):
    r = D(rate_pct) / 1200
    p = D(principal)
    emi = q2(p * r * (1 + r) ** tenor / ((1 + r) ** tenor - 1))
    bal, rows = p, []
    for k in range(1, tenor + 1):
        due = first_due + relativedelta(months=k - 1)
        if k == tenor:
            prin = bal
        else:
            prin = emi - q2(bal * r)
        rows.append({"inst_no": k, "due_date": due, "principal_due": q2(prin)})
        bal -= prin
    return emi, rows

# ------------------------------------------------------------------ harness
class Harness:
    def __init__(self, loan_id, spec, id_offset=0):
        self.id_offset = id_offset
        self.loan_id = loan_id
        self.spec = spec
        self.last = None            # last date the proc has been run to
        self.payments = []          # all payments inserted

    def create(self, with_payments=None):
        s = self.spec
        cur.execute("""INSERT INTO lms.loan (loan_id, customer_ref, product_code, branch_state,
            disb_date, principal_disbursed, rate_type, fixed_rate_pct, benchmark_code, spread_pct,
            reset_months, tenor_months, emi)
            VALUES (%(id)s, %(cust)s, %(product)s, %(state)s, %(disb)s, %(principal)s, %(rate_type)s,
                    %(fixed)s, %(bench)s, %(spread)s, %(reset)s, %(tenor)s, %(emi)s)""",
            dict(id=self.loan_id, cust=s["cust"], product=s["product"], state=s["state"],
                 disb=s["disb"], principal=s["principal"], rate_type=s["rate_type"],
                 fixed=s.get("fixed"), bench=s.get("bench"), spread=s.get("spread"),
                 reset=s.get("reset", 3), tenor=s["tenor"], emi=s["emi"]))
        for r in s["schedule"]:
            cur.execute("INSERT INTO lms.installment (loan_id, inst_no, due_date, principal_due) "
                        "VALUES (%s,%s,%s,%s)", (self.loan_id, r["inst_no"], r["due_date"], r["principal_due"]))
        for p in (with_payments or []):
            cur.execute("INSERT INTO lms.payment (payment_id, loan_id, pay_date, amount, pay_type) "
                        "VALUES (%s,%s,%s,%s,%s)", (p["payment_id"] + self.id_offset, self.loan_id, p["pay_date"], p["amount"], p["pay_type"]))

    def run_to(self, d):
        if self.last is not None and d <= self.last:
            return
        cur.execute("CALL lms.sp_loan_eod_process(%s::int, %s::date)", (self.loan_id, d))
        self.last = d

    def loan(self):
        cur.execute("SELECT * FROM lms.loan WHERE loan_id=%s", (self.loan_id,))
        return cur.fetchone()

    def inst(self, k):
        cur.execute("SELECT * FROM lms.installment WHERE loan_id=%s AND inst_no=%s", (self.loan_id, k))
        return cur.fetchone()

    def unpaid_billed(self):
        cur.execute("""SELECT coalesce(sum((principal_due-paid_principal)+(interest_due-paid_interest)),0) AS x
                       FROM lms.installment WHERE loan_id=%s AND interest_due IS NOT NULL""", (self.loan_id,))
        return cur.fetchone()["x"]

    def eff_due(self, d):
        cur.execute("SELECT lms.fn_next_business_day(%s::date, %s) AS x", (d, self.spec["state"]))
        return cur.fetchone()["x"]

    def pay(self, d, amount, ptype="REGULAR"):
        amount = q2(amount)
        if amount <= 0:
            return
        if self.last is not None and d <= self.last:
            d = self.last + timedelta(days=1)
        pid = next_pay_id()
        cur.execute("INSERT INTO lms.payment (payment_id, loan_id, pay_date, amount, pay_type) "
                    "VALUES (%s,%s,%s,%s,%s)", (pid + self.id_offset, self.loan_id, d, amount, ptype))
        self.payments.append({"payment_id": pid, "pay_date": d, "amount": amount, "pay_type": ptype})

# ------------------------------------------------------------------ behaviour engine
def drive(h, behaviours, tail_days=10):
    """behaviours[k-1] is a tuple per installment:
       ('ontime',) ('late', n) ('partial', frac) ('over', extra) ('skip',)
       ('prepay', amount, days_after) is a modifier given as a 2nd element: (kind..., modifier)
       ('foreclose', days_after)"""
    tenor = h.spec["tenor"]
    last_date = None
    for k in range(1, tenor + 1):
        row = h.inst(k)
        if row is None:
            break
        d = row["due_date"]
        last_date = d
        b = behaviours[k - 1] if k - 1 < len(behaviours) else ("ontime",)
        kind = b[0]
        h.run_to(d)
        eff = h.eff_due(d)
        base = max(d + timedelta(days=1), eff)
        row = h.inst(k)
        bill = (row["principal_due"] - row["paid_principal"]) + (row["interest_due"] - row["paid_interest"])
        if kind == "ontime":
            h.pay(base, bill)
        elif kind == "late":
            pay_date = max(base, eff + timedelta(days=b[1]))
            h.run_to(pay_date - timedelta(days=1))
            ln = h.loan()
            h.pay(pay_date, h.unpaid_billed() + ln["accrued_penal"] + ln["fees_outstanding"])
        elif kind == "partial":
            h.pay(base, bill * D(str(b[1])))
        elif kind == "over":
            h.pay(base, bill + D(str(b[1])))
        elif kind == "skip":
            pass
        elif kind == "prepay":
            h.pay(base, bill)
            h.pay(base + timedelta(days=b[2]), D(str(b[1])), "PREPAYMENT")
            pp_date = h.payments[-1]["pay_date"]
            h.run_to(pp_date)
            fees = h.loan()["fees_outstanding"]
            if fees > 0:                       # customer settles the prepayment fee next day
                h.pay(pp_date + timedelta(days=1), fees)
        elif kind == "foreclose":
            h.pay(base, bill)
            pay_date = base + timedelta(days=b[1])
            h.run_to(pay_date - timedelta(days=1))
            ln = h.loan()
            h.pay(pay_date, ln["principal_outstanding"] + ln["accrued_interest"] * 0, "PREPAYMENT")
            h.run_to(pay_date)
            ln = h.loan()
            h.pay(pay_date + timedelta(days=1), ln["fees_outstanding"])
            h.run_to(pay_date + timedelta(days=3))
            return h.last
        elif kind == "catchup":
            pay_date = max(base, eff + timedelta(days=b[1]))
            h.run_to(pay_date - timedelta(days=1))
            ln = h.loan()
            h.pay(pay_date, h.unpaid_billed() + ln["accrued_penal"] + ln["fees_outstanding"])
    end = (last_date or h.spec["disb"]) + timedelta(days=tail_days)
    h.run_to(end)
    return h.last

# ------------------------------------------------------------------ specs
def spec(cust, product, state, disb, principal, tenor, first_due, rate_type="FIXED", fixed=None,
         bench=None, spread=None, reset=3):
    if rate_type == "FIXED":
        rate0 = D(str(fixed))
    else:
        cur.execute("SELECT rate_pct FROM lms.benchmark_rate WHERE benchmark_code=%s AND effective_date<=%s "
                    "ORDER BY effective_date DESC LIMIT 1", (bench, disb))
        rate0 = cur.fetchone()["rate_pct"] + D(str(spread))
    emi, sched = make_schedule(principal, rate0, tenor, first_due)
    return dict(cust=cust, product=product, state=state, disb=disb, principal=D(str(principal)),
                rate_type=rate_type, fixed=None if fixed is None else D(str(fixed)),
                bench=bench, spread=None if spread is None else D(str(spread)), reset=reset,
                tenor=tenor, emi=emi, schedule=sched)

dt = date
CASES = []   # (case_id, scenario, description, spec, behaviours, tail)
def add(cid, scenario, desc, sp, beh=(), tail=10):
    CASES.append((cid, scenario, desc, sp, list(beh), tail))

add("C01", "on_time_act365", "Fixed home loan, ACT/365, every instalment paid on time",
    spec("CUST-0001", "HL-STD", "KA", dt(2024,1,10), 2500000, 12, dt(2024,2,10), fixed=8.5))
add("C02", "late_within_grace", "Paid 4 days after due date, inside the 5-day grace: no fee, no penal",
    spec("CUST-0002", "HL-STD", "KA", dt(2024,1,10), 1800000, 12, dt(2024,2,10), fixed=8.75),
    [("late",4),("late",4),("ontime",),("late",3)])
add("C03", "late_beyond_grace", "Paid 20 days late twice: late fee, GST, penal interest, SMA0",
    spec("CUST-0003", "HL-STD", "MH", dt(2024,1,10), 1800000, 12, dt(2024,2,10), fixed=9.0),
    [("ontime",),("ontime",),("late",20),("late",20)])
add("C04", "holiday_shift", "Due dates fall on Sundays, a 4th Saturday and Holi; paid on the next working day",
    spec("CUST-0004", "AL-ST", "KA", dt(2024,2,24), 900000, 10, dt(2024,3,24), fixed=10.5))
add("C05", "floating_reset_month_end", "Floating loan disbursed on the 31st; benchmark change lands in a reset window",
    spec("CUST-0005", "HL-STD", "TN", dt(2024,7,31), 3000000, 12, dt(2024,8,31), rate_type="FLOATING",
         bench="REPO", spread=2.5, reset=3))
add("C06", "thirty360_leap_feb", "30/360 personal loan across February 2024 (leap year)",
    spec("CUST-0006", "PL-FLX", "KA", dt(2024,1,5), 400000, 6, dt(2024,2,5), fixed=14.0))
add("C07", "thirty360_plain_feb", "30/360 personal loan across February 2025 (not a leap year)",
    spec("CUST-0007", "PL-FLX", "KA", dt(2025,1,5), 400000, 6, dt(2025,2,5), fixed=14.0))
add("C08", "on_time_act360", "Auto loan, ACT/360, on time",
    spec("CUST-0008", "AL-ST", "KA", dt(2024,1,15), 750000, 12, dt(2024,2,15), fixed=9.5))
add("C09", "npa_and_upgrade", "Five instalments skipped: SMA0, SMA1, SMA2, NPA with suspense, then a catch-up payment",
    spec("CUST-0009", "HL-STD", "KA", dt(2024,1,10), 2000000, 10, dt(2024,2,10), fixed=9.0),
    [("ontime",),("skip",),("skip",),("skip",),("skip",),("skip",),("catchup", 15)])
add("C10", "part_prepay_fixed", "Part prepayment of 300,000 on a fixed rate loan: 2% fee plus GST, tenure shortens",
    spec("CUST-0010", "HL-STD", "KA", dt(2024,1,10), 2500000, 12, dt(2024,2,10), fixed=8.5),
    [("ontime",),("ontime",),("prepay",300000,3)])
add("C11", "part_prepay_floating", "Part prepayment of 300,000 on a floating rate loan: no prepayment fee",
    spec("CUST-0011", "HL-STD", "KA", dt(2024,1,10), 2500000, 12, dt(2024,2,10), rate_type="FLOATING",
         bench="REPO", spread=2.0),
    [("ontime",),("ontime",),("prepay",300000,3)])
add("C12", "foreclosure", "Full prepayment on a fixed auto loan: 1.5% fee, fee paid next day, loan closes, broken-period interest written off",
    spec("CUST-0012", "AL-ST", "MH", dt(2024,1,15), 750000, 12, dt(2024,2,15), fixed=9.5),
    [("ontime",),("ontime",),("ontime",),("foreclose", 6)])
add("C13", "overpayment_advance", "One instalment overpaid by 5,000; the advance is used up on the next bill",
    spec("CUST-0013", "HL-STD", "KA", dt(2024,1,10), 1800000, 8, dt(2024,2,10), fixed=8.75),
    [("ontime",),("over",5000)])
add("C14", "partial_payment_pct_fee", "MSME loan, only 60% paid on two instalments: percentage late fee on the overdue part",
    spec("CUST-0014", "BL-MSM", "TN", dt(2024,1,10), 5000000, 10, dt(2024,2,10), fixed=12.0),
    [("ontime",),("partial",0.6),("partial",0.6)])
add("C15", "late_fee_cap_binds", "Large MSME loan: 0.75% late fee would exceed the 10,000 cap",
    spec("CUST-0015", "BL-MSM", "MH", dt(2024,1,10), 20000000, 12, dt(2024,2,10), fixed=11.5),
    [("ontime",),("late",25)])
add("C16", "flat_fee_only_product", "Auto loan with a flat late fee and no percentage or cap, 10 days late",
    spec("CUST-0016", "AL-ST", "KA", dt(2024,1,15), 600000, 8, dt(2024,2,15), fixed=10.0),
    [("ontime",),("ontime",),("late",10)])

# random cases
rng = random.Random(42)
PRODUCTS = ["HL-STD", "PL-FLX", "AL-ST", "BL-MSM"]
STATES = ["KA", "MH", "TN"]
for i in range(1, 21):
    prod = rng.choice(PRODUCTS)
    state = rng.choice(STATES)
    disb = dt(2024, 1, 1) + timedelta(days=rng.randint(0, 330))
    first_due = disb + relativedelta(months=1)
    principal = rng.randrange(150000, 4000000, 5000)
    tenor = rng.randint(6, 14)
    floating = rng.random() < 0.3
    if floating:
        sp = spec(f"CUST-R{i:03d}", prod, state, disb, principal, tenor, first_due, rate_type="FLOATING",
                  bench="REPO", spread=round(rng.uniform(1.5, 3.5), 2), reset=rng.choice([1, 3]))
    else:
        sp = spec(f"CUST-R{i:03d}", prod, state, disb, principal, tenor, first_due,
                  fixed=round(rng.uniform(7.5, 16.0), 2))
    beh = []
    prepaid = False
    for k in range(tenor):
        x = rng.random()
        if x < 0.68: beh.append(("ontime",))
        elif x < 0.82: beh.append(("late", rng.randint(1, 18)))
        elif x < 0.88: beh.append(("partial", rng.choice([0.4, 0.5, 0.75])))
        elif x < 0.93: beh.append(("skip",))
        elif x < 0.97: beh.append(("over", rng.choice([1000, 2500, 7500])))
        else:
            if not prepaid and k < tenor - 3:
                prepaid = True
                beh.append(("prepay", rng.choice([50000, 120000, 200000]), rng.randint(1, 8)))
            else:
                beh.append(("ontime",))
    add(f"R{i:02d}", "random", f"Randomised loan {i}: {prod}, {'floating' if floating else 'fixed'}, {tenor} months, mixed behaviour",
        sp, beh, tail=rng.randint(0, 40))

# ------------------------------------------------------------------ run
def jsonable(o):
    if isinstance(o, Decimal): return str(o)
    if isinstance(o, date): return o.isoformat()
    raise TypeError(type(o))

def fetch_output(loan_id):
    cur.execute("SELECT * FROM lms.loan WHERE loan_id=%s", (loan_id,)); loan = cur.fetchone()
    cur.execute("SELECT inst_no,due_date,principal_due,interest_due,paid_principal,paid_interest,late_fee_charged "
                "FROM lms.installment WHERE loan_id=%s ORDER BY inst_no", (loan_id,)); insts = cur.fetchall()
    cur.execute("SELECT entry_date,entry_type,amount,note FROM lms.loan_ledger WHERE loan_id=%s ORDER BY ledger_id", (loan_id,)); led = cur.fetchall()
    cur.execute("SELECT days_processed,interest_accrued,penal_accrued,fees_charged,payments_applied,dpd,asset_class "
                "FROM lms.loan_run_summary WHERE loan_id=%s ORDER BY as_of DESC LIMIT 1", (loan_id,)); summ = cur.fetchone()
    for k in ("loan_id", "customer_ref", "product_code", "branch_state", "disb_date", "principal_disbursed", "rate_type",
              "fixed_rate_pct", "benchmark_code", "spread_pct", "reset_months", "tenor_months", "emi"):
        loan.pop(k, None)
    return {"loan": loan, "installments": insts, "ledger": led, "summary": summ}

def norm(o):
    return json.loads(json.dumps(o, default=jsonable))

golden, report = [], []
for n, (cid, scenario, desc, sp, beh, tail) in enumerate(CASES, start=1):
    pass1_id, case_id_num = 5000 + n, 1000 + n
    h1 = Harness(pass1_id, sp, id_offset=500000)
    h1.create()
    as_of = drive(h1, beh, tail)
    out1 = norm(fetch_output(pass1_id))

    h2 = Harness(case_id_num, sp)
    h2.create(with_payments=h1.payments)
    cur.execute("CALL lms.sp_loan_eod_process(%s::int, %s::date)", (case_id_num, as_of))
    out2 = norm(fetch_output(case_id_num))

    same = out1["loan"] == out2["loan"] and out1["installments"] == out2["installments"]

    ledger_types = {}
    for l in out2["ledger"]:
        ledger_types[l["entry_type"]] = ledger_types.get(l["entry_type"], 0) + 1
    report.append((cid, scenario, as_of.isoformat(), out2["loan"]["asset_class"], out2["loan"]["status"],
                   out2["loan"]["dpd"], same, ledger_types))

    golden.append({
        "case_id": cid, "scenario": scenario, "description": desc,
        "inputs": {
            "loan": {"loan_id": case_id_num, "customer_ref": sp["cust"], "product_code": sp["product"],
                     "branch_state": sp["state"], "disb_date": sp["disb"], "principal_disbursed": sp["principal"],
                     "rate_type": sp["rate_type"], "fixed_rate_pct": sp["fixed"], "benchmark_code": sp["bench"],
                     "spread_pct": sp["spread"], "reset_months": sp["reset"], "tenor_months": sp["tenor"], "emi": sp["emi"]},
            "installments": sp["schedule"],
            "payments": h1.payments,
            "as_of": as_of,
        },
        "legacy_output": out2,
    })
    print(f"{cid:4} {scenario:28} as_of={as_of} class={out2['loan']['asset_class']:8} status={out2['loan']['status']:7} "
          f"dpd={out2['loan']['dpd']:3} pass1==pass2:{same}", flush=True)

# reference data
ref = {}
cur.execute("SELECT * FROM lms.loan_product ORDER BY product_code"); ref["products"] = cur.fetchall()
cur.execute("SELECT * FROM lms.benchmark_rate ORDER BY benchmark_code, effective_date"); ref["benchmark_rates"] = cur.fetchall()
cur.execute("SELECT hdate,state_code,description FROM lms.holiday ORDER BY hdate, state_code"); ref["holidays"] = cur.fetchall()
(OUT / "reference.json").write_text(json.dumps(norm(ref), indent=1))
(OUT / "golden_master.json").write_text(json.dumps(norm(golden), indent=1))
print("cases:", len(golden))
print("not equal pass1/pass2:", [r[0] for r in report if not r[6]])
all_types = {}
for r in report:
    for t, c in r[7].items(): all_types[t] = all_types.get(t, 0) + c
print("ledger entry types seen:", all_types)
