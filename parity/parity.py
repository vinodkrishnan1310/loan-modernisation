"""
Parity harness: replay each golden-master case through the converted code and diff.

Plain code finds every difference. Nothing here uses an LLM; the Grok mismatch
classifier is a separate component that reads the diffs this script emits.

Usage: python3 parity/parity.py [--module service/loan_eod.py] [--out evidence/parity_report.json]
"""
import argparse, importlib.util, json, pathlib, sys
from datetime import date
from decimal import Decimal as D, ROUND_HALF_UP

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent

ap = argparse.ArgumentParser()
ap.add_argument("--module", default=str(ROOT / "service" / "loan_eod.py"))
ap.add_argument("--out", default=str(ROOT / "evidence" / "parity_report.json"))
args = ap.parse_args()

spec = importlib.util.spec_from_file_location("loan_eod", args.module)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

reference = json.load(open(ROOT / "golden" / "reference.json"))
golden = json.load(open(ROOT / "golden" / "golden_master.json"))

def money(x):
    return str(D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP))

def norm_val(v, key=""):
    if v is None: return None
    if isinstance(v, bool): return v
    if isinstance(v, date): return v.isoformat()
    if isinstance(v, D): return str(v)
    return v

def money_or_same(k, v):
    # numeric(14,2) columns: show 2 dp
    if k in {"principal_outstanding","accrued_interest","accrued_penal","suspense_interest","fees_outstanding",
             "advance_balance","principal_due","interest_due","paid_principal","paid_interest","amount",
             "interest_accrued","penal_accrued","fees_charged","payments_applied"} and v is not None:
        return money(v)
    if k == "current_rate_pct" and v is not None:
        return str(D(v).quantize(D("0.0001")))
    return norm_val(v)

def norm_obj(o):
    if isinstance(o, dict):
        return {k: money_or_same(k, v) if not isinstance(v, (dict, list)) else norm_obj(v) for k, v in o.items()}
    if isinstance(o, list):
        return [norm_obj(x) for x in o]
    return norm_val(o)

def compare(cid, legacy, new):
    diffs = []
    for k in legacy["loan"]:
        a, b = legacy["loan"][k], new["loan"].get(k)
        if k == "last_run_date": continue
        if a != b: diffs.append({"path": f"loan.{k}", "legacy": a, "new": b})
    li, ni = legacy["installments"], new["installments"]
    if len(li) != len(ni):
        diffs.append({"path": "installments.count", "legacy": len(li), "new": len(ni)})
    for a, b in zip(li, ni):
        for k in a:
            if a[k] != b.get(k):
                diffs.append({"path": f"installments[{a['inst_no']}].{k}", "legacy": a[k], "new": b.get(k)})
    ll, nl = legacy["ledger"], new["ledger"]
    first = None
    for i, (a, b) in enumerate(zip(ll, nl)):
        if a != b:
            first = {"index": i, "legacy": a, "new": b}
            break
    if first is None and len(ll) != len(nl):
        i = min(len(ll), len(nl))
        first = {"index": i, "legacy": ll[i] if i < len(ll) else None, "new": nl[i] if i < len(nl) else None}
    if first:
        diffs.append({"path": "ledger.first_divergence", **first})
    diffs.append({"path": "ledger.count", "legacy": len(ll), "new": len(nl)}) if len(ll) != len(nl) else None
    for k in legacy["summary"]:
        if legacy["summary"][k] != new["summary"].get(k):
            diffs.append({"path": f"summary.{k}", "legacy": legacy["summary"][k], "new": new["summary"].get(k)})
    return diffs

report, n_fail = [], 0
for c in golden:
    try:
        out = mod.process_loan(reference, c["inputs"])
        new = norm_obj(out)
        # ledger and installments shapes
        diffs = compare(c["case_id"], c["legacy_output"], new)
        status = "MATCH" if not diffs else "MISMATCH"
    except Exception as e:
        diffs, status = [{"path": "exception", "legacy": None, "new": f"{type(e).__name__}: {e}"}], "ERROR"
    if status != "MATCH": n_fail += 1
    report.append({"case_id": c["case_id"], "scenario": c["scenario"], "status": status, "diffs": diffs})
    print(f"{c['case_id']:4} {c['scenario']:28} {status:9} {len(diffs)} diff(s)")

json.dump(report, open(args.out, "w"), indent=1, default=str)
print(f"\n{len(golden) - n_fail} of {len(golden)} cases match; report: {args.out}")
