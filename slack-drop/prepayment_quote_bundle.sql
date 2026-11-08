-- ============================================================================
-- lms.sp_prepayment_quote: payoff quote for one loan on a given date.
-- Brightfield Cooperative Bank, loan servicing (synthetic). Read only, no helpers.
--
--   2012-05  RK   First version: principal + accrued interest.
--   2013-02  RK   Prepayment fee for fixed-rate loans (LMS-0601).
--   2016-09  SM   Fee waived in the last 3 months of the tenor (LMS-1320).
--   2018-04  SM   Quote valid for two days (LMS-1544).
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
    v_loan lms.loan%ROWTYPE;
    v_prod lms.loan_product%ROWTYPE;
    v_rate numeric;
    v_days int;
    v_left int;
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

    o_principal := coalesce(v_loan.principal_outstanding, v_loan.principal_disbursed);

    -- Rate: fixed, or the latest benchmark on the quote date plus the spread.
    v_rate := CASE WHEN v_loan.rate_type = 'FIXED' THEN v_loan.fixed_rate_pct
                   ELSE coalesce((SELECT b.rate_pct FROM lms.benchmark_rate b
                                   WHERE b.benchmark_code = v_loan.benchmark_code
                                     AND b.effective_date <= p_quote_date
                                   ORDER BY b.effective_date DESC LIMIT 1), 0)
                        + coalesce(v_loan.spread_pct, 0) END;

    -- Interest since the last run, rounded once at the end.
    v_days := greatest(p_quote_date - coalesce(v_loan.last_run_date, v_loan.disb_date), 0);
    o_interest := v_loan.accrued_interest
                + round(o_principal * v_rate / 100 * v_days
                        / CASE v_prod.day_count WHEN 'ACT365' THEN 365 ELSE 360 END, 2);
    o_penal := v_loan.accrued_penal;
    o_fees  := v_loan.fees_outstanding;

    -- Prepayment fee: fixed-rate loans only, unless 3 months or fewer remain.
    o_prepay_fee := 0;
    v_left := v_loan.tenor_months
            - (extract(year from age(p_quote_date, v_loan.disb_date)) * 12
             + extract(month from age(p_quote_date, v_loan.disb_date)));
    IF v_loan.rate_type = 'FIXED' AND v_prod.prepay_fee_pct IS NOT NULL AND v_left > 3 THEN
        o_prepay_fee := round(o_principal * v_prod.prepay_fee_pct / 100, 2);
    END IF;

    o_gst   := round((o_prepay_fee + o_fees) * 18 / 100, 2);
    o_total := greatest(o_principal + o_interest + o_penal + o_fees + o_prepay_fee + o_gst
                        - v_loan.advance_balance, 0);
    o_valid_until := p_quote_date + 2;
END;
$$;
