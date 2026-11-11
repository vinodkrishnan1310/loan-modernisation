"""Adapter smoke test with a fake connection (no database)."""
import pathlib
import sys
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

psycopg = pytest.importorskip("psycopg")
from engine_service import run_rate_reset_notice  # noqa: E402


class _Rows(list):
    def fetchall(self):
        return self

    def fetchone(self):
        return self[0] if self else None


def test_run_rate_reset_notice_posts_ledger_and_returns_counts():
    loan = {
        "loan_id": 8001,
        "customer_ref": "GEN",
        "product_code": "HL-STD",
        "branch_state": "MH",
        "disb_date": date(2024, 1, 10),
        "principal_disbursed": Decimal("1000000.00"),
        "rate_type": "FLOATING",
        "fixed_rate_pct": None,
        "benchmark_code": "REPO",
        "spread_pct": Decimal("2.5000"),
        "reset_months": 3,
        "tenor_months": 60,
        "emi": Decimal("1.00"),
        "status": "ACTIVE",
        "last_run_date": None,
        "principal_outstanding": Decimal("1000000.00"),
        "accrued_interest": Decimal(0),
        "accrued_penal": Decimal(0),
        "fees_outstanding": Decimal(0),
        "advance_balance": Decimal(0),
        "asset_class": "STANDARD",
    }
    benchmarks = [
        {
            "benchmark_code": "REPO",
            "effective_date": date(2024, 2, 8),
            "rate_pct": Decimal("6.5000"),
        },
        {
            "benchmark_code": "REPO",
            "effective_date": date(2024, 10, 9),
            "rate_pct": Decimal("6.7500"),
        },
    ]

    def execute(sql, params=None):
        if "benchmark_rate" in sql:
            return _Rows(benchmarks)
        if "FROM lms.loan WHERE" in sql:
            return _Rows([loan])
        if "FROM lms.loan_ledger" in sql:
            return _Rows([])
        conn.executed_inserts.append((sql, params))
        return MagicMock()

    conn = MagicMock()
    conn.executed_inserts = []
    conn.execute = execute

    out = run_rate_reset_notice(conn, date(2024, 9, 20), 30)
    assert out == {"p_posted": 1, "p_skipped": 0}
    assert len(conn.executed_inserts) == 1
    assert "INSERT INTO lms.loan_ledger" in conn.executed_inserts[0][0]
