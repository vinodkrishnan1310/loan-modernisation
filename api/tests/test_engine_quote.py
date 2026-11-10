"""Adapter test with a fake connection. No database."""

import json
import pathlib
import sys
from datetime import date
from decimal import Decimal

import psycopg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from engine_service import run_prepayment_quote  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
REFERENCE = json.loads((ROOT / "golden" / "reference.json").read_text(encoding="utf-8"))
Q01 = json.loads((ROOT / "golden" / "quote_cases.json").read_text(encoding="utf-8"))[0]


class _Result:
    def __init__(self, one=None, many=None):
        self._one = one
        self._many = [] if many is None else many

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._many)


class FakeConn:
    def __init__(self, loan):
        self.loan = loan
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        text = self.calls[-1][0].lower()
        if text.startswith("insert into lms.loan_ledger"):
            return _Result()
        if "from lms.loan_product" in text:
            return _Result(many=REFERENCE["products"])
        if "from lms.benchmark_rate" in text:
            return _Result(many=REFERENCE["benchmark_rates"])
        if "from lms.holiday" in text:
            return _Result(many=REFERENCE["holidays"])
        if "from lms.loan" in text:
            return _Result(one=self.loan)
        raise AssertionError(sql)


def _db_loan():
    """Same figures as Q01, with the types psycopg returns."""
    loan = dict(Q01["inputs"]["loan"])
    loan["loan_id"] = int(loan["loan_id"])
    loan["disb_date"] = date.fromisoformat(loan["disb_date"])
    loan["reset_months"] = int(loan["reset_months"])
    loan["tenor_months"] = int(loan["tenor_months"])
    for key in (
        "principal_disbursed",
        "fixed_rate_pct",
        "emi",
        "principal_outstanding",
        "accrued_interest",
        "accrued_penal",
        "fees_outstanding",
        "advance_balance",
    ):
        if loan[key] is not None:
            loan[key] = Decimal(loan[key])
    return loan


def test_run_prepayment_quote_returns_result_without_ledger_post():
    conn = FakeConn(_db_loan())
    result = run_prepayment_quote(conn, 8001, date(2024, 6, 15))
    assert set(result) == {
        "o_principal",
        "o_interest",
        "o_penal",
        "o_fees",
        "o_prepay_fee",
        "o_gst",
        "o_total",
        "o_valid_until",
    }
    assert result["o_total"] == Decimal("1062312.33")
    assert result["o_valid_until"] == date(2024, 6, 17)
    assert not any(sql.lower().startswith("insert") for sql, _params in conn.calls)
    assert all("create " not in sql.lower() and "alter " not in sql.lower() for sql, _params in conn.calls)


def test_run_prepayment_quote_raises_and_does_not_post_when_refused():
    loan = _db_loan()
    loan["status"] = "CLOSED"
    conn = FakeConn(loan)
    with pytest.raises(psycopg.errors.RaiseException) as caught:
        run_prepayment_quote(conn, 8001, date(2024, 6, 15))
    assert str(caught.value).splitlines()[0] == "LMS-021 loan 8001 is CLOSED and cannot be quoted"
    assert not any(sql.lower().startswith("insert") for sql, _params in conn.calls)


def test_run_prepayment_quote_raises_when_the_loan_is_missing():
    conn = FakeConn(None)
    with pytest.raises(psycopg.errors.RaiseException) as caught:
        run_prepayment_quote(conn, 8001, date(2024, 6, 15))
    assert str(caught.value).splitlines()[0] == "LMS-001 loan 8001 not found"
    assert not any(sql.lower().startswith("insert") for sql, _params in conn.calls)
