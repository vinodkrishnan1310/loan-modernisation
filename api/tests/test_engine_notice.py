"""Adapter test with a fake connection. No database."""

import json
import pathlib
import sys
from datetime import date
from decimal import Decimal

import psycopg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from engine_service import run_rate_reset_notice  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
REFERENCE = json.loads((ROOT / "golden" / "reference.json").read_text(encoding="utf-8"))
N11 = json.loads((ROOT / "golden" / "notice_cases.json").read_text(encoding="utf-8"))[10]


class _Result:
    def __init__(self, one=None, many=None):
        self._one = one
        self._many = [] if many is None else many

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._many)


class FakeConn:
    def __init__(self, loans, prior=None):
        self.loans = loans
        self.prior = [] if prior is None else prior
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
        if "entry_type = 'reset_notice'" in text:
            return _Result(many=self.prior)
        if "rate_type = 'floating'" in text:
            active = [
                dict(loan)
                for loan in self.loans
                if loan.get("status") == "ACTIVE" and loan.get("rate_type") == "FLOATING"
            ]
            return _Result(many=sorted(active, key=lambda row: int(row["loan_id"])))
        raise AssertionError(sql)


def _db_loans():
    rows = []
    for raw in N11["inputs"]["loans"]:
        loan = dict(raw)
        loan["loan_id"] = int(loan["loan_id"])
        loan["disb_date"] = date.fromisoformat(loan["disb_date"])
        loan["reset_months"] = int(loan["reset_months"])
        loan["tenor_months"] = int(loan["tenor_months"])
        for key in (
            "principal_disbursed",
            "fixed_rate_pct",
            "spread_pct",
            "emi",
            "principal_outstanding",
            "accrued_interest",
            "accrued_penal",
            "fees_outstanding",
            "advance_balance",
        ):
            if loan.get(key) is not None:
                loan[key] = Decimal(loan[key])
        rows.append(loan)
    return rows


def test_run_rate_reset_notice_posts_expected_rows():
    conn = FakeConn(_db_loans())
    result = run_rate_reset_notice(conn, date(2024, 9, 20), 30)
    assert result == {"p_posted": 2, "p_skipped": 1}
    inserts = [params for sql, params in conn.calls if sql.lower().startswith("insert")]
    assert len(inserts) == 2
    assert inserts[0][0:3] == (8001, "2024-10-10", "RESET_NOTICE")
    assert all("create " not in sql.lower() and "alter " not in sql.lower() for sql, _params in conn.calls)


def test_run_rate_reset_notice_raises_for_invalid_window():
    conn = FakeConn(_db_loans())
    with pytest.raises(psycopg.errors.RaiseException) as caught:
        run_rate_reset_notice(conn, date(2024, 9, 20), 120)
    assert str(caught.value).splitlines()[0] == "LMS-031 window 120 days is outside 1 to 90"
    assert not any(sql.lower().startswith("insert") for sql, _params in conn.calls)
