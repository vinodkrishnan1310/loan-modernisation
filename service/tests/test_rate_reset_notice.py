"""Replay recorded rate-reset notice cases through the pure function."""

import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pytest

from service.rate_reset_notice import notices

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = json.loads((ROOT / "golden" / "reference.json").read_text(encoding="utf-8"))
CASES = json.loads((ROOT / "golden" / "notice_cases.json").read_text(encoding="utf-8"))
MONEY = {"amount"}


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
        return {
            key: _norm(key, item) if not isinstance(item, (dict, list)) else _norm_obj(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_norm_obj(item) for item in value]
    return value


@pytest.mark.parametrize("case", CASES, ids=[case["case_id"] for case in CASES])
def test_notices_matches_recorded_case(case):
    got = _norm_obj(notices(REFERENCE, case["inputs"]))
    want = _norm_obj(case["legacy_output"])
    assert got == want
