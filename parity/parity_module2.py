"""
Parity harness for the two extra procedures. Replays recorded legacy cases through the converted code.

Plain code finds every difference. The Grok mismatch classifier reads the report this writes.

  python3 parity/parity_module2.py --proc quote  [--module service/prepayment_quote.py]  [--out evidence/parity_quote.json]
  python3 parity/parity_module2.py --proc notice [--module service/rate_reset_notice.py] [--out evidence/parity_notice.json]
"""
import argparse, importlib.util, json, pathlib
from datetime import date
from decimal import Decimal as D, ROUND_HALF_UP

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROCS = {
    "quote":  {"golden": "quote_cases.json",  "module": "service/prepayment_quote.py",  "fn": "quote",
               "out": "evidence/parity_quote.json"},
    "notice": {"golden": "notice_cases.json", "module": "service/rate_reset_notice.py", "fn": "notices",
               "out": "evidence/parity_notice.json"},
}
ap = argparse.ArgumentParser()
ap.add_argument("--proc", required=True, choices=PROCS)
ap.add_argument("--module")
ap.add_argument("--out")
args = ap.parse_args()
cfg = PROCS[args.proc]
module_path = args.module or str(ROOT / cfg["module"])
out_path = args.out or str(ROOT / cfg["out"])

spec = importlib.util.spec_from_file_location("impl", module_path)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
fn = getattr(mod, cfg["fn"])
reference = json.load(open(ROOT / "golden" / "reference.json"))
golden = json.load(open(ROOT / "golden" / cfg["golden"]))

MONEY = {"o_principal", "o_interest", "o_penal", "o_fees", "o_prepay_fee", "o_gst", "o_total", "amount"}


def norm(k, v):
    if v is None: return None
    if isinstance(v, date): return v.isoformat()
    if k in MONEY: return str(D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP))
    return str(v) if not isinstance(v, (int, str)) or isinstance(v, bool) else v


def norm_obj(o):
    if isinstance(o, dict): return {k: norm(k, v) if not isinstance(v, (dict, list)) else norm_obj(v) for k, v in o.items()}
    if isinstance(o, list): return [norm_obj(x) for x in o]
    return o


def compare(legacy, new):
    diffs = []
    for k in ("error",):
        if legacy.get(k) != new.get(k):
            diffs.append({"path": k, "legacy": legacy.get(k), "new": new.get(k)})
    for k in ("posted", "skipped"):
        if k in legacy and legacy[k] != new.get(k):
            diffs.append({"path": k, "legacy": legacy[k], "new": new.get(k)})
    for k, v in legacy.get("result", {}).items():
        nv = new.get("result", {}).get(k)
        if v != nv: diffs.append({"path": f"result.{k}", "legacy": v, "new": nv})
    ll, nl = legacy["ledger"], new.get("ledger", [])
    first = None
    for i, (a, b) in enumerate(zip(ll, nl)):
        if a != b:
            first = {"index": i, "legacy": a, "new": b}; break
    if first is None and len(ll) != len(nl):
        i = min(len(ll), len(nl))
        first = {"index": i, "legacy": ll[i] if i < len(ll) else None, "new": nl[i] if i < len(nl) else None}
    if first: diffs.append({"path": "ledger.first_divergence", **first})
    if len(ll) != len(nl): diffs.append({"path": "ledger.count", "legacy": len(ll), "new": len(nl)})
    return diffs


report, n_fail = [], 0
for c in golden:
    try:
        diffs = compare(norm_obj(c["legacy_output"]), norm_obj(fn(reference, c["inputs"])))
        status = "MATCH" if not diffs else "MISMATCH"
    except Exception as e:
        diffs, status = [{"path": "exception", "legacy": None, "new": f"{type(e).__name__}: {e}"}], "ERROR"
    if status != "MATCH": n_fail += 1
    report.append({"case_id": c["case_id"], "scenario": c["scenario"], "status": status, "diffs": diffs})
    print(f"{c['case_id']:4} {c['scenario']:28} {status:9} {len(diffs)} diff(s)")
pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
json.dump(report, open(out_path, "w"), indent=1, default=str)
print(f"\n{len(golden) - n_fail} of {len(golden)} cases match; report: {out_path}")
