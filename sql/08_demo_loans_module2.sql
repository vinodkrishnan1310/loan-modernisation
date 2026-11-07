-- Demo loans for the quote and rate-reset-notice procedures. Safe to re-run.
--   9003: fixed-rate home loan, for POST /loans/9003/prepayment-quote
--   9004: floating-rate home loan whose next reset (2024-10-09) changes the rate, for POST /rate-reset-notices
DELETE FROM lms.loan_ledger WHERE loan_id IN (9003, 9004);
DELETE FROM lms.loan WHERE loan_id IN (9003, 9004);
INSERT INTO lms.loan (loan_id, customer_ref, product_code, branch_state, disb_date, principal_disbursed, rate_type, fixed_rate_pct, benchmark_code, spread_pct, reset_months, tenor_months, emi)
VALUES (9003, 'DEMO-9003', 'HL-STD', 'MH', '2024-01-10', 1800000.00, 'FIXED', 9.0000, NULL, NULL, 3, 60, 37364.00);
INSERT INTO lms.loan (loan_id, customer_ref, product_code, branch_state, disb_date, principal_disbursed, rate_type, fixed_rate_pct, benchmark_code, spread_pct, reset_months, tenor_months, emi)
VALUES (9004, 'DEMO-9004', 'HL-STD', 'KA', '2024-07-09', 2400000.00, 'FLOATING', NULL, 'REPO', 2.5000, 3, 120, 31450.00);
