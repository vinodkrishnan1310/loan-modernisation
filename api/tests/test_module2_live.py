"""Live checks of the two extra endpoints against the legacy engine. Skipped when the database is not reachable."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
psycopg = pytest.importorskip("psycopg")
from fastapi.testclient import TestClient  # noqa: E402
from main import DATABASE_URL, app  # noqa: E402


def _db_up():
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=2) as c:
            c.execute("SELECT 1 FROM lms.loan WHERE loan_id = 9003")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="database with demo loans is not reachable")
client = TestClient(app)


def test_quote_for_demo_loan():
    r = client.post("/loans/9003/prepayment-quote", json={"quote_date": "2024-06-15"})
    assert r.status_code == 200
    assert r.json()["valid_until"] == "2024-06-17"


def test_quote_rejects_date_before_disbursal():
    r = client.post("/loans/9003/prepayment-quote", json={"quote_date": "2023-01-01"})
    assert r.status_code == 422 and r.json()["detail"].startswith("LMS-002")


def test_notice_window_is_validated():
    r = client.post("/rate-reset-notices", json={"as_of": "2024-09-25", "window_days": 400})
    assert r.status_code == 422 and r.json()["detail"].startswith("LMS-031")
