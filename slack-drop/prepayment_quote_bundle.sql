-- Bundle for the Slack drop: shared helper functions followed by the procedure.
-- ============================================================================
-- Helper routines used by sp_loan_eod_process.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- fn_is_business_day: bank working day test.
-- Sundays, 2nd/4th Saturdays and listed holidays are non-working.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION lms.fn_is_business_day(p_date date, p_state text)
RETURNS boolean
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    v_dow int := extract(dow from p_date);      -- 0 = Sunday
    v_nth int;
BEGIN
    IF v_dow = 0 THEN
        RETURN false;
    END IF;

    IF v_dow = 6 THEN
        v_nth := ceil(extract(day from p_date) / 7.0);
        IF v_nth IN (2, 4) THEN
            RETURN false;
        END IF;
    END IF;

    IF EXISTS (SELECT 1 FROM lms.holiday h
                WHERE h.hdate = p_date
                  AND h.state_code IN ('ALL', p_state)) THEN
        RETURN false;
    END IF;

    RETURN true;
END;
$$;

-- ----------------------------------------------------------------------------
-- fn_next_business_day: first working day on or after p_date.
-- Gives up after 10 steps (long holiday runs) and returns the last date tried.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION lms.fn_next_business_day(p_date date, p_state text)
RETURNS date
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    v_d date := p_date;
    v_i int  := 0;
BEGIN
    WHILE NOT lms.fn_is_business_day(v_d, p_state) AND v_i < 10 LOOP
        v_d := v_d + 1;
        v_i := v_i + 1;
    END LOOP;
    RETURN v_d;
END;
$$;

-- ----------------------------------------------------------------------------
-- fn_rate_for_day: annual rate (%) applicable on a given day.
-- FIXED loans use fixed_rate_pct. FLOATING loans reset every reset_months
-- from disbursement; the rate is the benchmark in force on the reset date
-- plus the spread.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION lms.fn_rate_for_day(p_loan_id int, p_day date)
RETURNS numeric
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    l         lms.loan%ROWTYPE;
    v_months  int;
    v_reset   date;
    v_bench   numeric;
BEGIN
    SELECT * INTO l FROM lms.loan WHERE loan_id = p_loan_id;

    IF l.rate_type = 'FIXED' THEN
        RETURN l.fixed_rate_pct;
    END IF;

    v_months := extract(year  from age(p_day, l.disb_date)) * 12
              + extract(month from age(p_day, l.disb_date));
    v_months := (v_months / l.reset_months) * l.reset_months;
    v_reset  := (l.disb_date + (v_months || ' months')::interval)::date;

    SELECT b.rate_pct INTO v_bench
      FROM lms.benchmark_rate b
     WHERE b.benchmark_code = l.benchmark_code
       AND b.effective_date <= v_reset
     ORDER BY b.effective_date DESC
     LIMIT 1;

    IF v_bench IS NULL THEN
        RETURN l.fixed_rate_pct;
    END IF;

    RETURN v_bench + coalesce(l.spread_pct, 0);
END;
$$;

-- ----------------------------------------------------------------------------
-- fn_apply_to_installments: spend an amount on billed, unpaid installments,
-- oldest first, interest before principal. Returns what was spent and what
-- is left. Does not touch the loan row.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION lms.fn_apply_to_installments(
    p_loan_id int,
    p_amount  numeric,
    OUT p_rest      numeric,
    OUT p_int_paid  numeric,
    OUT p_prin_paid numeric)
LANGUAGE plpgsql
AS $$
DECLARE
    r record;
    a numeric;
BEGIN
    p_rest      := p_amount;
    p_int_paid  := 0;
    p_prin_paid := 0;

    FOR r IN
        SELECT * FROM lms.installment
         WHERE loan_id = p_loan_id
           AND interest_due IS NOT NULL
           AND ((interest_due - paid_interest) > 0
             OR (principal_due - paid_principal) > 0)
         ORDER BY inst_no
    LOOP
        EXIT WHEN p_rest <= 0;

        a := least(p_rest, r.interest_due - r.paid_interest);
        IF a > 0 THEN
            UPDATE lms.installment
               SET paid_interest = paid_interest + a
             WHERE loan_id = p_loan_id AND inst_no = r.inst_no;
            p_rest     := p_rest - a;
            p_int_paid := p_int_paid + a;
        END IF;

        a := least(p_rest, r.principal_due - r.paid_principal);
        IF a > 0 THEN
            UPDATE lms.installment
               SET paid_principal = paid_principal + a
             WHERE loan_id = p_loan_id AND inst_no = r.inst_no;
            p_rest      := p_rest - a;
            p_prin_paid := p_prin_paid + a;
        END IF;
    END LOOP;
END;
$$;

-- ----------------------------------------------------------------------------
-- sp_post_ledger: one place to write ledger rows.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE lms.sp_post_ledger(
    p_loan_id int, p_date date, p_type text, p_amount numeric, p_note text)
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO lms.loan_ledger (loan_id, entry_date, entry_type, amount, note)
    VALUES (p_loan_id, p_date, p_type, p_amount, p_note);
END;
$$;

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
