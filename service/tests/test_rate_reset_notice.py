"""Replay golden notice cases through the pure function."""
import json
import pathlib
import sys
from datetime import date
from decimal import Decimal as D, ROUND_HALF_UP

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from service.rate_reset_notice import notices  # noqa: E402

GOLDEN = json.load(open(ROOT / "golden" / "notice_cases.json"))
REFERENCE = json.load(open(ROOT / "golden" / "reference.json"))

MONEY = {"amount"}


def norm(k, v):
    if v is None:
        return None
    if isinstance(v, date):
        return v.isoformat()
    if k in MONEY:
        return str(D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP))
    return str(v) if not isinstance(v, (int, str)) or isinstance(v, bool) else v


def norm_obj(o):
    if isinstance(o, dict):
        return {
            k: norm(k, v) if not isinstance(v, (dict, list)) else norm_obj(v) for k, v in o.items()
        }
    if isinstance(o, list):
        return [norm_obj(x) for x in o]
    return o


def compare(legacy, new):
    diffs = []
    for k in ("error",):
        if legacy.get(k) != new.get(k):
            diffs.append({"path": k, "legacy": legacy.get(k), "new": new.get(k)})
    for k in ("posted", "skipped"):
        if k in legacy and legacy[k] != new.get(k):
            diffs.append({"path": k, "legacy": legacy[k], "new": new.get(k)})
    ll, nl = legacy["ledger"], new.get("ledger", [])
    first = None
    for i, (a, b) in enumerate(zip(ll, nl)):
        if a != b:
            first = {"index": i, "legacy": a, "new": b}
            break
    if first is None and len(ll) != len(nl):
        i = min(len(ll), len(nl))
        first = {
            "index": i,
            "legacy": ll[i] if i < len(ll) else None,
            "new": nl[i] if i < len(nl) else None,
        }
    if first:
        diffs.append({"path": "ledger.first_divergence", **first})
    if len(ll) != len(nl):
        diffs.append({"path": "ledger.count", "legacy": len(ll), "new": len(nl)})
    return diffs


def test_notice_golden_cases_match_legacy():
    failures = []
    for case in GOLDEN:
        out = notices(REFERENCE, case["inputs"])
        diffs = compare(norm_obj(case["legacy_output"]), norm_obj(out))
        if diffs:
            failures.append((case["case_id"], diffs))
    assert not failures, failures
