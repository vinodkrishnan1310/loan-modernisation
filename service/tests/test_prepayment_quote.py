"""Replay golden quote cases through the pure quote engine."""
from __future__ import annotations

import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from service.prepayment_quote import quote  # noqa: E402

REFERENCE = json.loads((ROOT / "golden" / "reference.json").read_text(encoding="utf-8"))
CASES = json.loads((ROOT / "golden" / "quote_cases.json").read_text(encoding="utf-8"))

MONEY = ("o_principal", "o_interest", "o_penal", "o_fees", "o_prepay_fee", "o_gst", "o_total", "amount")


def _norm_value(key: str, value):
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    if key in MONEY:
        return str(Decimal(value).quantize(Decimal("0.01")))
    return value


def _norm(obj, parent_key: str = ""):
    if isinstance(obj, dict):
        return {k: _norm(v, k) if not isinstance(v, (dict, list)) else _norm(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_norm(x, parent_key) for x in obj]
    return _norm_value(parent_key, obj)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_golden_case(case):
    got = _norm(quote(REFERENCE, case["inputs"]))
    want = _norm(case["legacy_output"])
    assert got == want
