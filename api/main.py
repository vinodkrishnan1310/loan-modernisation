"""
Loan EOD API.

Baseline (before modernisation): every call runs the legacy stored procedure
lms.sp_loan_eod_process. After the conversion PR is merged, ENGINE=service runs
the converted Python engine instead. The HTTP contract must not change; the
contract test (api/tests/test_contract.py) enforces that.
"""
import datetime as dt
import os
from decimal import Decimal

import psycopg
from fastapi import FastAPI, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres@localhost:5433/loandb")
ENGINE = os.environ.get("ENGINE", "legacy")        # legacy | service

app = FastAPI(title="Loan EOD API", version="1.0.0")


class EodRequest(BaseModel):
    as_of: dt.date


class LoanState(BaseModel):
    loan_id: int
    status: str
    last_run_date: dt.date | None
    asset_class: str
    dpd: int
    current_rate_pct: Decimal | None
    principal_outstanding: Decimal
    accrued_interest: Decimal
    accrued_penal: Decimal
    suspense_interest: Decimal
    fees_outstanding: Decimal
    advance_balance: Decimal


class EodResponse(BaseModel):
    engine: str
    as_of: dt.date
    ledger_entries_added: int
    state: LoanState


class QuoteRequest(BaseModel):
    quote_date: dt.date


class QuoteResponse(BaseModel):
    engine: str
    loan_id: int
    quote_date: dt.date
    principal: Decimal
    interest: Decimal
    penal: Decimal
    fees: Decimal
    prepay_fee: Decimal
    gst: Decimal
    total: Decimal
    valid_until: dt.date


class NoticeRequest(BaseModel):
    as_of: dt.date
    window_days: int = 30


class NoticeResponse(BaseModel):
    engine: str
    as_of: dt.date
    window_days: int
    posted: int
    skipped: int


class Health(BaseModel):
    status: str
    engine: str


STATE_SQL = """SELECT loan_id, status, last_run_date, asset_class, dpd, current_rate_pct,
                      coalesce(principal_outstanding, principal_disbursed) AS principal_outstanding,
                      accrued_interest, accrued_penal, suspense_interest, fees_outstanding, advance_balance
                 FROM lms.loan WHERE loan_id = %s"""


def connect():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def run_engine(conn, loan_id: int, as_of: dt.date) -> None:
    if ENGINE == "legacy":
        conn.execute("CALL lms.sp_loan_eod_process(%s::int, %s::date)", (loan_id, as_of))
    elif ENGINE == "service":
        try:
            from engine_service import run_eod          # added by the conversion PR
        except ImportError:
            raise HTTPException(status_code=501, detail="service engine is not built yet")
        run_eod(conn, loan_id, as_of)
    else:
        raise HTTPException(status_code=500, detail=f"unknown ENGINE {ENGINE!r}")


def run_quote(conn, loan_id: int, quote_date: dt.date) -> dict:
    if ENGINE == "legacy":
        return conn.execute("SELECT * FROM lms.sp_prepayment_quote(%s::int, %s::date)", (loan_id, quote_date)).fetchone()
    if ENGINE == "service":
        try:
            from engine_service import run_prepayment_quote   # added by the conversion PR
        except ImportError:
            raise HTTPException(status_code=501, detail="service engine is not built yet")
        return run_prepayment_quote(conn, loan_id, quote_date)
    raise HTTPException(status_code=500, detail=f"unknown ENGINE {ENGINE!r}")


def run_notices(conn, as_of: dt.date, window_days: int) -> dict:
    if ENGINE == "legacy":
        return conn.execute("CALL lms.sp_rate_reset_notice(%s::date, %s::int)", (as_of, window_days)).fetchone()
    if ENGINE == "service":
        try:
            from engine_service import run_rate_reset_notice  # added by the conversion PR
        except ImportError:
            raise HTTPException(status_code=501, detail="service engine is not built yet")
        return run_rate_reset_notice(conn, as_of, window_days)
    raise HTTPException(status_code=500, detail=f"unknown ENGINE {ENGINE!r}")


@app.get("/health", response_model=Health)
def health():
    with connect() as conn:
        conn.execute("SELECT 1")
    return Health(status="ok", engine=ENGINE)


@app.get("/loans/{loan_id}", response_model=LoanState)
def get_loan(loan_id: int):
    with connect() as conn:
        row = conn.execute(STATE_SQL, (loan_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="loan not found")
    return row


@app.post("/loans/{loan_id}/eod", response_model=EodResponse)
def run_loan_eod(loan_id: int, body: EodRequest):
    with connect() as conn:
        before = conn.execute("SELECT count(*) AS n FROM lms.loan_ledger WHERE loan_id = %s", (loan_id,)).fetchone()["n"]
        try:
            run_engine(conn, loan_id, body.as_of)
        except psycopg.errors.RaiseException as e:          # LMS-001 ... LMS-005 from the procedure
            raise HTTPException(status_code=422, detail=str(e).splitlines()[0])
        after = conn.execute("SELECT count(*) AS n FROM lms.loan_ledger WHERE loan_id = %s", (loan_id,)).fetchone()["n"]
        state = conn.execute(STATE_SQL, (loan_id,)).fetchone()
    if state is None:
        raise HTTPException(status_code=404, detail="loan not found")
    return EodResponse(engine=ENGINE, as_of=body.as_of, ledger_entries_added=after - before, state=state)


@app.post("/loans/{loan_id}/prepayment-quote", response_model=QuoteResponse)
def prepayment_quote(loan_id: int, body: QuoteRequest):
    with connect() as conn:
        try:
            r = run_quote(conn, loan_id, body.quote_date)
        except psycopg.errors.RaiseException as e:          # LMS-001, LMS-002, LMS-003, LMS-021
            raise HTTPException(status_code=422, detail=str(e).splitlines()[0])
    return QuoteResponse(engine=ENGINE, loan_id=loan_id, quote_date=body.quote_date, principal=r["o_principal"],
                         interest=r["o_interest"], penal=r["o_penal"], fees=r["o_fees"],
                         prepay_fee=r["o_prepay_fee"], gst=r["o_gst"], total=r["o_total"],
                         valid_until=r["o_valid_until"])


@app.post("/rate-reset-notices", response_model=NoticeResponse)
def rate_reset_notices(body: NoticeRequest):
    """Runs over every active floating-rate loan in the database."""
    with connect() as conn:
        try:
            r = run_notices(conn, body.as_of, body.window_days)
        except psycopg.errors.RaiseException as e:          # LMS-005, LMS-031
            raise HTTPException(status_code=422, detail=str(e).splitlines()[0])
    return NoticeResponse(engine=ENGINE, as_of=body.as_of, window_days=body.window_days,
                          posted=r["p_posted"], skipped=r["p_skipped"])
