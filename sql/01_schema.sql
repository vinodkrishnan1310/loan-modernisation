-- ============================================================================
-- LMS legacy schema (synthetic). Brightfield Cooperative Bank, loan servicing.
-- Stand-in for a legacy relational platform; all data is made up.
-- ============================================================================
DROP SCHEMA IF EXISTS lms CASCADE;
CREATE SCHEMA lms;

CREATE TABLE lms.loan_product (
    product_code    text PRIMARY KEY,
    product_name    text NOT NULL,
    day_count       text NOT NULL CHECK (day_count IN ('ACT365','ACT360','30360')),
    grace_days      int  NOT NULL DEFAULT 0,
    penal_rate_pct  numeric(6,3) NOT NULL,
    late_fee_flat   numeric(12,2),
    late_fee_pct    numeric(6,3),
    late_fee_cap    numeric(12,2),
    prepay_fee_pct  numeric(6,3),
    npa_dpd         int
);

CREATE TABLE lms.benchmark_rate (
    benchmark_code  text NOT NULL,
    effective_date  date NOT NULL,
    rate_pct        numeric(7,4) NOT NULL,
    PRIMARY KEY (benchmark_code, effective_date)
);

CREATE TABLE lms.holiday (
    hdate           date NOT NULL,
    state_code      text NOT NULL,          -- 'ALL' = national
    description     text,
    PRIMARY KEY (hdate, state_code)
);

CREATE TABLE lms.loan (
    loan_id               int PRIMARY KEY,
    customer_ref          text NOT NULL,
    product_code          text NOT NULL REFERENCES lms.loan_product,
    branch_state          text NOT NULL,
    disb_date             date NOT NULL,
    principal_disbursed   numeric(14,2) NOT NULL,
    rate_type             text NOT NULL CHECK (rate_type IN ('FIXED','FLOATING')),
    fixed_rate_pct        numeric(7,4),
    benchmark_code        text,
    spread_pct            numeric(7,4),
    reset_months          int DEFAULT 3,
    tenor_months          int NOT NULL,
    emi                   numeric(14,2) NOT NULL,
    status                text NOT NULL DEFAULT 'ACTIVE',
    -- running state, maintained by sp_loan_eod_process
    last_run_date         date,
    current_rate_pct      numeric(7,4),
    principal_outstanding numeric(14,2),
    accrued_interest      numeric(14,2) NOT NULL DEFAULT 0,
    accrued_penal         numeric(14,2) NOT NULL DEFAULT 0,
    suspense_interest     numeric(14,2) NOT NULL DEFAULT 0,
    fees_outstanding      numeric(14,2) NOT NULL DEFAULT 0,
    advance_balance       numeric(14,2) NOT NULL DEFAULT 0,
    asset_class           text NOT NULL DEFAULT 'STANDARD',
    dpd                   int NOT NULL DEFAULT 0
);

CREATE TABLE lms.installment (
    loan_id             int NOT NULL REFERENCES lms.loan,
    inst_no             int NOT NULL,
    due_date            date NOT NULL,
    principal_due       numeric(14,2) NOT NULL,
    interest_due        numeric(14,2),               -- NULL until billed on due_date
    paid_principal      numeric(14,2) NOT NULL DEFAULT 0,
    paid_interest       numeric(14,2) NOT NULL DEFAULT 0,
    late_fee_charged    boolean NOT NULL DEFAULT false,
    PRIMARY KEY (loan_id, inst_no)
);

CREATE TABLE lms.payment (
    payment_id   int PRIMARY KEY,
    loan_id      int NOT NULL REFERENCES lms.loan,
    pay_date     date NOT NULL,
    amount       numeric(14,2) NOT NULL CHECK (amount > 0),
    pay_type     text NOT NULL DEFAULT 'REGULAR' CHECK (pay_type IN ('REGULAR','PREPAYMENT')),
    applied      boolean NOT NULL DEFAULT false
);

CREATE TABLE lms.loan_ledger (
    ledger_id    bigserial PRIMARY KEY,
    loan_id      int NOT NULL,
    entry_date   date NOT NULL,
    entry_type   text NOT NULL,
    amount       numeric(14,2) NOT NULL,
    note         text
);

CREATE TABLE lms.loan_run_summary (
    loan_id            int NOT NULL,
    as_of              date NOT NULL,
    days_processed     int NOT NULL,
    interest_accrued   numeric(14,2) NOT NULL,
    penal_accrued      numeric(14,2) NOT NULL,
    fees_charged       numeric(14,2) NOT NULL,
    payments_applied   numeric(14,2) NOT NULL,
    dpd                int NOT NULL,
    asset_class        text NOT NULL,
    PRIMARY KEY (loan_id, as_of)
);

CREATE TABLE lms.loan_error_log (
    err_id     bigserial PRIMARY KEY,
    loan_id    int,
    as_of      date,
    sqlstate   text,
    message    text,
    logged_at  timestamptz NOT NULL DEFAULT now()
);
