-- ============================================================================
-- lms.sp_rate_reset_notice: advance notice of floating-rate resets.
-- Brightfield Cooperative Bank, loan servicing (synthetic). No helper functions.
--
--   2015-03  SM   First version: list resets due in the next 30 days (LMS-0988).
--   2017-11  AP   Skip changes under 0.25 percentage points (LMS-1402).
--   2019-06  AP   Skip non-performing loans (LMS-1655).
--   2021-01  AP   Do not post the same notice twice (LMS-1790).
--
-- For every active floating-rate loan whose next reset falls inside the window,
-- posts a RESET_NOTICE row to the ledger. The amount is the new rate.
-- ============================================================================
CREATE OR REPLACE PROCEDURE lms.sp_rate_reset_notice(
    p_as_of       date,
    p_window_days int,
    INOUT p_posted  int DEFAULT 0,
    INOUT p_skipped int DEFAULT 0)
LANGUAGE plpgsql
AS $$
DECLARE
    r        lms.loan%ROWTYPE;
    v_months int;
    v_next   date;
    v_notice date;
    v_old    numeric;
    v_new    numeric;
BEGIN
    IF p_window_days IS NULL OR p_window_days < 1 OR p_window_days > 90 THEN
        RAISE EXCEPTION 'LMS-031 window % days is outside 1 to 90', p_window_days;
    END IF;
    p_posted  := 0;
    p_skipped := 0;

    FOR r IN SELECT * FROM lms.loan
              WHERE status = 'ACTIVE' AND rate_type = 'FLOATING' ORDER BY loan_id
    LOOP
        IF r.asset_class = 'NPA' THEN
            p_skipped := p_skipped + 1;
            CONTINUE;
        END IF;
        IF r.benchmark_code IS NULL THEN
            RAISE EXCEPTION 'LMS-005 floating loan % has no benchmark', r.loan_id;
        END IF;

        -- Months since disbursement, then the start of the next reset cycle.
        v_months := extract(year from age(p_as_of, r.disb_date)) * 12
                  + extract(month from age(p_as_of, r.disb_date));
        v_months := (v_months / r.reset_months) * r.reset_months + r.reset_months;
        v_next   := (r.disb_date + (v_months || ' months')::interval)::date;
        IF v_next > p_as_of + p_window_days THEN
            CONTINUE;
        END IF;

        -- The notice is dated on the next weekday on or after the reset date.
        v_notice := v_next + CASE extract(dow FROM v_next) WHEN 6 THEN 2 WHEN 0 THEN 1 ELSE 0 END;

        -- New rate: benchmark on the reset date. Current rate: benchmark at the cycle start.
        v_new := coalesce((SELECT rate_pct FROM lms.benchmark_rate
                            WHERE benchmark_code = r.benchmark_code AND effective_date <= v_next
                            ORDER BY effective_date DESC LIMIT 1), 0) + coalesce(r.spread_pct, 0);
        v_old := coalesce((SELECT rate_pct FROM lms.benchmark_rate
                            WHERE benchmark_code = r.benchmark_code
                              AND effective_date <= (r.disb_date + ((v_months - r.reset_months) || ' months')::interval)::date
                            ORDER BY effective_date DESC LIMIT 1), 0) + coalesce(r.spread_pct, 0);

        IF abs(v_new - v_old) < 0.25
           OR EXISTS (SELECT 1 FROM lms.loan_ledger WHERE loan_id = r.loan_id
                       AND entry_type = 'RESET_NOTICE' AND note LIKE 'reset ' || v_next || '%') THEN
            p_skipped := p_skipped + 1;
            CONTINUE;
        END IF;

        INSERT INTO lms.loan_ledger (loan_id, entry_date, entry_type, amount, note)
        VALUES (r.loan_id, v_notice, 'RESET_NOTICE', v_new,
                'reset ' || v_next || ' from ' || v_old || ' to ' || v_new);
        p_posted := p_posted + 1;
    END LOOP;
END;
$$;
