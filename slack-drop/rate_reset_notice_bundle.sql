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
-- lms.sp_rate_reset_notice: advance notice of floating-rate resets.
-- Brightfield Cooperative Bank, loan servicing (synthetic).
--
-- Change history
--   2015-03  SM   First version: list resets due in the next 30 days (LMS-0988).
--   2017-11  AP   Skip changes under 0.25 percentage points (LMS-1402).
--   2019-06  AP   Skip non-performing loans (LMS-1655).
--   2021-01  AP   Do not post the same notice twice (LMS-1790).
--
-- For every active floating-rate loan whose next reset falls inside the window,
-- posts a RESET_NOTICE row to the ledger. The amount is the new rate.
-- Safe to run daily. Needs no changes to the loan rows.
-- ============================================================================
CREATE OR REPLACE PROCEDURE lms.sp_rate_reset_notice(
    p_as_of       date,
    p_window_days int,
    INOUT p_posted  int DEFAULT 0,
    INOUT p_skipped int DEFAULT 0)
LANGUAGE plpgsql
AS $$
DECLARE
    c_min_change constant numeric := 0.25;
    r            record;
    v_months     int;
    v_next_reset date;
    v_notice_day date;
    v_old_rate   numeric;
    v_new_rate   numeric;
    v_bench      numeric;
BEGIN
    IF p_window_days IS NULL OR p_window_days < 1 OR p_window_days > 90 THEN
        RAISE EXCEPTION 'LMS-031 window % days is outside 1 to 90', p_window_days;
    END IF;
    p_posted  := 0;
    p_skipped := 0;

    FOR r IN
        SELECT * FROM lms.loan
         WHERE status = 'ACTIVE'
           AND rate_type = 'FLOATING'
         ORDER BY loan_id
    LOOP
        IF r.asset_class = 'NPA' THEN
            p_skipped := p_skipped + 1;
            CONTINUE;
        END IF;
        IF r.benchmark_code IS NULL THEN
            RAISE EXCEPTION 'LMS-005 floating loan % has no benchmark', r.loan_id;
        END IF;

        -- Months since disbursement, then the start of the next reset cycle.
        v_months := extract(year  from age(p_as_of, r.disb_date)) * 12
                  + extract(month from age(p_as_of, r.disb_date));
        v_months := (v_months / r.reset_months) * r.reset_months + r.reset_months;
        v_next_reset := (r.disb_date + (v_months || ' months')::interval)::date;

        IF v_next_reset > p_as_of + p_window_days THEN
            CONTINUE;
        END IF;

        -- The notice goes out on the business day on or after the reset date.
        v_notice_day := lms.fn_next_business_day(v_next_reset, r.branch_state);

        -- Benchmark in force on the reset date itself.
        SELECT b.rate_pct INTO v_bench
          FROM lms.benchmark_rate b
         WHERE b.benchmark_code = r.benchmark_code
           AND b.effective_date <= v_next_reset
         ORDER BY b.effective_date DESC
         LIMIT 1;
        v_new_rate := coalesce(v_bench, 0) + coalesce(r.spread_pct, 0);
        v_old_rate := lms.fn_rate_for_day(r.loan_id, p_as_of);

        IF abs(v_new_rate - v_old_rate) < c_min_change THEN
            p_skipped := p_skipped + 1;
            CONTINUE;
        END IF;

        -- One notice per reset cycle.
        IF EXISTS (SELECT 1 FROM lms.loan_ledger
                    WHERE loan_id = r.loan_id
                      AND entry_type = 'RESET_NOTICE'
                      AND note LIKE 'reset ' || v_next_reset || '%') THEN
            p_skipped := p_skipped + 1;
            CONTINUE;
        END IF;

        CALL lms.sp_post_ledger(r.loan_id, v_notice_day, 'RESET_NOTICE', v_new_rate,
                                'reset ' || v_next_reset || ' from ' || v_old_rate
                                || ' to ' || v_new_rate);
        p_posted := p_posted + 1;
    END LOOP;
END;
$$;
