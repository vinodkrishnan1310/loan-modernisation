"""Replay recorded prepayment quotes through the pure function."""

import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pytest

from service.prepayment_quote import quote

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = json.loads((ROOT / "golden" / "reference.json").read_text(encoding="utf-8"))
CASES = json.loads((ROOT / "golden" / "quote_cases.json").read_text(encoding="utf-8"))
MONEY = {"o_principal", "o_interest", "o_penal", "o_fees", "o_prepay_fee", "o_gst", "o_total", "amount"}


def _norm(key, value):
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    if key in MONEY:
        return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if isinstance(value, bool) or isinstance(value, (int, str)):
        return value
    return str(value)


def _norm_obj(value):
    if isinstance(value, dict):
        return {key: _norm(key, item) if not isinstance(item, (dict, list)) else _norm_obj(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_norm_obj(item) for item in value]
    return value


@pytest.mark.parametrize("case", CASES, ids=[case["case_id"] for case in CASES])
def test_quote_matches_recorded_case(case):
    got = _norm_obj(quote(REFERENCE, case["inputs"]))
    expected = _norm_obj(case["legacy_output"])
    assert got == expected


def test_unknown_product_is_refused_without_a_ledger_row():
    case = json.loads(json.dumps(CASES[0]))
    case["inputs"]["loan"]["product_code"] = "NO-SUCH"
    got = quote(REFERENCE, case["inputs"])
    assert got == {"error": "LMS-003 product NO-SUCH not found", "ledger": []}


def test_null_accrued_interest_is_not_treated_as_zero():
    case = json.loads(json.dumps(CASES[0]))
    case["inputs"]["loan"]["accrued_interest"] = None
    got = quote(REFERENCE, case["inputs"])
    assert "error" not in got
    assert got["result"]["o_interest"] is None
    assert got["result"]["o_total"] is None
    assert got["ledger"][0]["amount"] is None


def test_missing_loan_names_the_id():
    got = quote(REFERENCE, {"loan": None, "loan_id": 8001, "quote_date": "2024-06-15"})
    assert got == {"error": "LMS-001 loan 8001 not found", "ledger": []}
