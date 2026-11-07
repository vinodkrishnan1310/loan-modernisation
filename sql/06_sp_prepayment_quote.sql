-- ============================================================================
-- lms.sp_prepayment_quote: payoff quote for a loan on a given date.
-- Brightfield Cooperative Bank, loan servicing (synthetic).
--
-- Change history
--   2012-05  RK   First version: principal + accrued interest.
--   2013-02  RK   Added prepayment fee for fixed-rate loans (ticket LMS-0601).
--   2016-09  SM   Fee waived in the last 3 months of the tenor (LMS-1320).
--   2018-04  SM   Quote validity: two business days (LMS-1544).
--
-- Reads the loan, product and rate tables. Writes one QUOTE row to the ledger.
-- Does not change the loan. Run it before the end-of-day job for the same date.
-- ============================================================================
CREATE OR REPLACE FUNCTION lms.sp_prepayment_quote(
    p_loan_id    int,
    p_quote_date date,
    OUT o_principal   numeric,
    OUT o_interest    numeric,
    OUT o_penal       numeric,
    OUT o_fees        numeric,
    OUT o_prepay_fee  numeric,
    OUT o_gst         numeric,
    OUT o_total       numeric,
    OUT o_valid_until date)
LANGUAGE plpgsql
AS $$
DECLARE
    c_gst_rate  constant numeric := 18;
    v_loan      lms.loan%ROWTYPE;
    v_prod      lms.loan_product%ROWTYPE;
    v_from      date;
    v_days      int;
    v_basis     numeric;
    v_rate      numeric;
    v_months_in int;
    v_months_left int;
BEGIN
    SELECT * INTO v_loan FROM lms.loan WHERE loan_id = p_loan_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'LMS-001 loan % not found', p_loan_id;
    END IF;
    IF v_loan.status <> 'ACTIVE' THEN
        RAISE EXCEPTION 'LMS-021 loan % is % and cannot be quoted', p_loan_id, v_loan.status;
    END IF;
    IF p_quote_date < v_loan.disb_date THEN
        RAISE EXCEPTION 'LMS-002 quote date % is before disbursement %', p_quote_date, v_loan.disb_date;
    END IF;

    SELECT * INTO v_prod FROM lms.loan_product WHERE product_code = v_loan.product_code;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'LMS-003 product % not found', v_loan.product_code;
    END IF;

    o_principal := coalesce(v_loan.principal_outstanding, v_loan.principal_disbursed);

    -- Interest since the last end-of-day run. Rounded once, at the end.
    v_from  := coalesce(v_loan.last_run_date, v_loan.disb_date);
    v_days  := greatest(p_quote_date - v_from, 0);
    v_basis := CASE v_prod.day_count WHEN 'ACT365' THEN 365 ELSE 360 END;
    v_rate  := lms.fn_rate_for_day(p_loan_id, p_quote_date);
    o_interest := v_loan.accrued_interest + round(o_principal * v_rate / 100 * v_days / v_basis, 2);

    -- Penal interest already accrued stays as it is. Nothing new is added in a quote.
    o_penal := v_loan.accrued_penal;
    o_fees  := v_loan.fees_outstanding;

    -- Prepayment fee: fixed-rate loans only, on the principal being paid off.
    o_prepay_fee := 0;
    IF v_loan.rate_type = 'FIXED' AND v_prod.prepay_fee_pct IS NOT NULL THEN
        v_months_in   := extract(year  from age(p_quote_date, v_loan.disb_date)) * 12
                       + extract(month from age(p_quote_date, v_loan.disb_date));
        v_months_left := v_loan.tenor_months - v_months_in;
        IF v_months_left > 3 THEN
            o_prepay_fee := round(o_principal * v_prod.prepay_fee_pct / 100, 2);
        END IF;
    END IF;

    o_gst := round((o_prepay_fee + o_fees) * c_gst_rate / 100, 2);

    -- An advance balance on the loan reduces what the customer pays.
    o_total := o_principal + o_interest + o_penal + o_fees + o_prepay_fee + o_gst
             - v_loan.advance_balance;
    IF o_total < 0 THEN
        o_total := 0;
    END IF;

    -- Valid for two business days in the branch state.
    o_valid_until := lms.fn_next_business_day(
                         lms.fn_next_business_day(p_quote_date, v_loan.branch_state) + 1,
                         v_loan.branch_state);

    CALL lms.sp_post_ledger(p_loan_id, p_quote_date, 'QUOTE', o_total,
                            'valid until ' || o_valid_until);
END;
$$;
