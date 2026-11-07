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
