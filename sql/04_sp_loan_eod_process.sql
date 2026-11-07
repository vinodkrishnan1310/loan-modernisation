-- ============================================================================
-- sp_loan_eod_process
--
-- Loan servicing end-of-day processor. Brings one loan from its last run date
-- up to p_as_of, one calendar day at a time:
--   rate -> billing -> payments -> accrual -> overdue scan -> DPD -> closure
--
-- Called nightly by the batch scheduler: CALL lms.sp_loan_eod_process(id, date)
--
-- Change history
--   2009-03  RK   Initial version, fixed rate loans only
--   2011-08  RK   Added 30/360 for personal loans (ticket LMS-0412)
--   2013-01  SM   Floating rate resets
--   2014-06  SM   Late fee + GST. Penal interest compounds monthly (LMS-1187)
--   2016-02  AP   NPA reversal to suspense
--   2017-09  AP   Prepayment fee on fixed rate loans only (LMS-1544)
--   2019-04  AP   Foreclosure: no interest for the broken period (LMS-2231)
--   2021-11  VN   Error log table. DO NOT remove the day loop, finance
--                 reconciles against it.
-- ============================================================================
CREATE OR REPLACE PROCEDURE lms.sp_loan_eod_process(p_loan_id int, p_as_of date)
LANGUAGE plpgsql
AS $$
DECLARE
    v_loan        lms.loan%ROWTYPE;
    v_prod        lms.loan_product%ROWTYPE;
    v_day         date;
    v_from        date;
    v_rate        numeric(7,4);
    v_prev_rate   numeric(7,4);
    v_acc         numeric(12,3);          -- daily accrual, 3 dp
    v_acc_post    numeric(12,2);
    v_factor      int;
    v_pay         record;
    v_inst        record;
    v_wf          record;
    v_rem         numeric(14,2);
    v_alloc       numeric(14,2);
    v_overdue     numeric(14,2);
    v_penal_day   numeric(12,2);
    v_eff_due     date;
    v_fee         numeric(12,2);
    v_gst         numeric(12,2);
    v_oldest      date;
    v_dpd         int;
    v_class       text;
    v_prepaid     numeric(14,2);
    v_trim        numeric(14,2);
    v_cut         numeric(14,2);
    v_unpaid      int;
    v_days        int           := 0;
    v_sum_int     numeric(14,2) := 0;
    v_sum_penal   numeric(14,2) := 0;
    v_sum_fees    numeric(14,2) := 0;
    v_sum_paid    numeric(14,2) := 0;
    c_gst_rate    constant numeric := 18;
BEGIN
    -- ------------------------------------------------------------------
    -- 1. Load the loan and its product, validate
    -- ------------------------------------------------------------------
    SELECT * INTO v_loan
      FROM lms.loan
     WHERE loan_id = p_loan_id
       FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'LMS-001 loan % not found', p_loan_id;
    END IF;

    IF v_loan.status = 'CLOSED' THEN
        RETURN;
    END IF;

    SELECT * INTO v_prod
      FROM lms.loan_product
     WHERE product_code = v_loan.product_code;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'LMS-003 product % not found', v_loan.product_code;
    END IF;

    IF p_as_of < v_loan.disb_date THEN
        RAISE EXCEPTION 'LMS-002 as_of % is before disbursement %',
                        p_as_of, v_loan.disb_date;
    END IF;

    IF v_loan.rate_type = 'FLOATING' AND v_loan.benchmark_code IS NULL THEN
        RAISE EXCEPTION 'LMS-005 floating loan % has no benchmark', p_loan_id;
    END IF;

    v_from := coalesce(v_loan.last_run_date, v_loan.disb_date);

    -- already processed up to this date, nothing to do
    IF p_as_of <= v_from THEN
        RETURN;
    END IF;

    IF v_loan.last_run_date IS NULL THEN
        v_loan.principal_outstanding := v_loan.principal_disbursed;
        v_loan.asset_class := 'STANDARD';
        v_loan.dpd := 0;
    END IF;

    v_prev_rate := v_loan.current_rate_pct;

    -- ------------------------------------------------------------------
    -- 2. Day loop
    -- ------------------------------------------------------------------
    v_day := v_from;

    WHILE v_day < p_as_of LOOP
        v_day  := v_day + 1;
        v_days := v_days + 1;

        -- --------------------------------------------------------------
        -- 2a. Rate for the day
        -- --------------------------------------------------------------
        v_rate := lms.fn_rate_for_day(p_loan_id, v_day);

        IF v_rate IS NULL OR v_rate < 0 OR v_rate > 60 THEN
            RAISE EXCEPTION 'LMS-004 bad rate % for loan % on %',
                            v_rate, p_loan_id, v_day;
        END IF;

        IF v_prev_rate IS DISTINCT FROM v_rate THEN
            IF v_prev_rate IS NOT NULL THEN
                CALL lms.sp_post_ledger(p_loan_id, v_day, 'RATE_RESET', v_rate,
                                        'from ' || v_prev_rate || ' to ' || v_rate);
            END IF;
            v_prev_rate := v_rate;
        END IF;

        -- --------------------------------------------------------------
        -- 2b. Billing: on the contractual due date, the interest accrued
        --     since the previous bill becomes the interest part of the
        --     instalment. NPA loans have nothing in accrued_interest (it
        --     sits in suspense) so they bill zero interest.
        -- --------------------------------------------------------------
        FOR v_inst IN
            SELECT * FROM lms.installment
             WHERE loan_id = p_loan_id
               AND due_date = v_day
               AND interest_due IS NULL
             ORDER BY inst_no
        LOOP
            UPDATE lms.installment
               SET interest_due = v_loan.accrued_interest
             WHERE loan_id = p_loan_id
               AND inst_no = v_inst.inst_no;

            CALL lms.sp_post_ledger(p_loan_id, v_day, 'BILL_INTEREST',
                                    v_loan.accrued_interest,
                                    'inst ' || v_inst.inst_no);
            v_loan.accrued_interest := 0;

            -- advance from an earlier overpayment is used up straight away
            IF v_loan.advance_balance > 0 THEN
                SELECT * INTO v_wf
                  FROM lms.fn_apply_to_installments(p_loan_id, v_loan.advance_balance);

                v_loan.principal_outstanding :=
                    v_loan.principal_outstanding - v_wf.p_prin_paid;

                CALL lms.sp_post_ledger(p_loan_id, v_day, 'ADVANCE_APPLIED',
                                        v_loan.advance_balance - v_wf.p_rest,
                                        'inst ' || v_inst.inst_no);
                v_loan.advance_balance := v_wf.p_rest;
            END IF;
        END LOOP;

        -- --------------------------------------------------------------
        -- 2c. Payments received today.
        --     Waterfall: fees, penal, interest, principal, then surplus.
        -- --------------------------------------------------------------
        FOR v_pay IN
            SELECT * FROM lms.payment
             WHERE loan_id = p_loan_id
               AND pay_date = v_day
               AND NOT applied
             ORDER BY payment_id
        LOOP
            v_rem      := v_pay.amount;
            v_sum_paid := v_sum_paid + v_pay.amount;

            -- fees (late fee, prepayment fee, and the GST on them)
            v_alloc := least(v_rem, v_loan.fees_outstanding);
            IF v_alloc > 0 THEN
                v_loan.fees_outstanding := v_loan.fees_outstanding - v_alloc;
                v_rem := v_rem - v_alloc;
                CALL lms.sp_post_ledger(p_loan_id, v_day, 'PAY_FEES', v_alloc,
                                        'payment ' || v_pay.payment_id);
            END IF;

            -- penal interest
            v_alloc := least(v_rem, v_loan.accrued_penal);
            IF v_alloc > 0 THEN
                v_loan.accrued_penal := v_loan.accrued_penal - v_alloc;
                v_rem := v_rem - v_alloc;
                CALL lms.sp_post_ledger(p_loan_id, v_day, 'PAY_PENAL', v_alloc,
                                        'payment ' || v_pay.payment_id);
            END IF;

            -- billed instalments, interest before principal
            SELECT * INTO v_wf
              FROM lms.fn_apply_to_installments(p_loan_id, v_rem);

            IF v_wf.p_int_paid > 0 THEN
                CALL lms.sp_post_ledger(p_loan_id, v_day, 'PAY_INTEREST',
                                        v_wf.p_int_paid,
                                        'payment ' || v_pay.payment_id);
            END IF;
            IF v_wf.p_prin_paid > 0 THEN
                v_loan.principal_outstanding :=
                    v_loan.principal_outstanding - v_wf.p_prin_paid;
                CALL lms.sp_post_ledger(p_loan_id, v_day, 'PAY_PRINCIPAL',
                                        v_wf.p_prin_paid,
                                        'payment ' || v_pay.payment_id);
            END IF;
            v_rem := v_wf.p_rest;

            -- surplus: part-prepayment or advance
            IF v_rem > 0 THEN
                IF v_pay.pay_type = 'PREPAYMENT' THEN
                    v_prepaid := least(v_rem, v_loan.principal_outstanding);

                    IF v_prepaid > 0 THEN
                        -- fixed rate loans pay a prepayment fee, floating do not
                        IF v_loan.rate_type = 'FIXED'
                           AND v_prod.prepay_fee_pct IS NOT NULL THEN
                            v_fee := round(v_prepaid * v_prod.prepay_fee_pct / 100, 2);
                            v_gst := round(v_fee * c_gst_rate / 100, 2);
                            v_loan.fees_outstanding :=
                                v_loan.fees_outstanding + v_fee + v_gst;
                            v_sum_fees := v_sum_fees + v_fee + v_gst;
                            CALL lms.sp_post_ledger(p_loan_id, v_day, 'PREPAY_FEE',
                                                    v_fee, 'on ' || v_prepaid);
                            CALL lms.sp_post_ledger(p_loan_id, v_day, 'GST_ON_FEE',
                                                    v_gst, 'prepay fee');
                        END IF;

                        v_loan.principal_outstanding :=
                            v_loan.principal_outstanding - v_prepaid;
                        v_rem := v_rem - v_prepaid;
                        CALL lms.sp_post_ledger(p_loan_id, v_day, 'PREPAYMENT',
                                                v_prepaid,
                                                'payment ' || v_pay.payment_id);

                        -- keep the EMI, shorten the tenure: take the prepaid
                        -- amount off the tail of the unbilled schedule
                        v_trim := v_prepaid;
                        FOR v_inst IN
                            SELECT * FROM lms.installment
                             WHERE loan_id = p_loan_id
                               AND interest_due IS NULL
                             ORDER BY inst_no DESC
                        LOOP
                            EXIT WHEN v_trim <= 0;
                            v_cut := least(v_trim, v_inst.principal_due);
                            IF v_cut >= v_inst.principal_due THEN
                                DELETE FROM lms.installment
                                 WHERE loan_id = p_loan_id
                                   AND inst_no = v_inst.inst_no;
                            ELSE
                                UPDATE lms.installment
                                   SET principal_due = principal_due - v_cut
                                 WHERE loan_id = p_loan_id
                                   AND inst_no = v_inst.inst_no;
                            END IF;
                            v_trim := v_trim - v_cut;
                        END LOOP;
                    END IF;
                END IF;

                IF v_rem > 0 THEN
                    v_loan.advance_balance := v_loan.advance_balance + v_rem;
                    CALL lms.sp_post_ledger(p_loan_id, v_day, 'ADVANCE_CREDIT',
                                            v_rem, 'payment ' || v_pay.payment_id);
                END IF;
            END IF;

            UPDATE lms.payment SET applied = true
             WHERE payment_id = v_pay.payment_id;
        END LOOP;

        -- --------------------------------------------------------------
        -- 2d. Interest accrual for the day
        --     ACT365 and ACT360 are plain day counts. 30/360 is done day
        --     by day: the 31st accrues nothing and the last day of February
        --     accrues the missing days so every month totals 30.
        -- --------------------------------------------------------------
        IF v_loan.principal_outstanding > 0 THEN
            v_factor := 1;

            IF v_prod.day_count = '30360' THEN
                IF extract(day from v_day) = 31 THEN
                    v_factor := 0;
                ELSIF extract(month from v_day) = 2
                  AND extract(month from v_day + 1) = 3 THEN
                    v_factor := 30 - extract(day from v_day)::int + 1;
                END IF;
            END IF;

            IF v_prod.day_count = 'ACT365' THEN
                v_acc := v_loan.principal_outstanding * v_rate / 100 / 365;
            ELSIF v_prod.day_count = 'ACT360' THEN
                v_acc := v_loan.principal_outstanding * v_rate / 100 / 360;
            ELSE
                v_acc := v_loan.principal_outstanding * v_rate / 100 / 360 * v_factor;
            END IF;

            v_acc_post := round(v_acc, 2);

            IF v_loan.asset_class = 'NPA' THEN
                v_loan.suspense_interest := v_loan.suspense_interest + v_acc_post;
            ELSE
                v_loan.accrued_interest := v_loan.accrued_interest + v_acc_post;
            END IF;
            v_sum_int := v_sum_int + v_acc_post;
        END IF;

        -- --------------------------------------------------------------
        -- 2e. Overdue scan: late fee once per instalment, penal interest
        --     daily. An instalment is overdue once today is past its due
        --     date (moved to the next working day if it falls on a holiday)
        --     plus the grace days of the product.
        --     Penal interest compounds monthly on the penal balance.
        -- --------------------------------------------------------------
        FOR v_inst IN
            SELECT * FROM lms.installment
             WHERE loan_id = p_loan_id
               AND interest_due IS NOT NULL
               AND ((principal_due - paid_principal)
                  + (interest_due  - paid_interest)) > 0
             ORDER BY inst_no
        LOOP
            v_eff_due := lms.fn_next_business_day(v_inst.due_date, v_loan.branch_state);

            IF v_day > v_eff_due + coalesce(v_prod.grace_days, 0) THEN
                v_overdue := (v_inst.principal_due - v_inst.paid_principal)
                           + (v_inst.interest_due  - v_inst.paid_interest);

                -- late fee: greater of flat and percentage, capped, plus GST
                IF NOT v_inst.late_fee_charged THEN
                    v_fee := greatest(v_prod.late_fee_flat,
                                      round(v_overdue * v_prod.late_fee_pct / 100, 2));
                    v_fee := least(v_fee, v_prod.late_fee_cap);
                    v_fee := coalesce(v_fee, 0);

                    IF v_fee > 0 THEN
                        v_gst := round(v_fee * c_gst_rate / 100, 2);
                        v_loan.fees_outstanding :=
                            v_loan.fees_outstanding + v_fee + v_gst;
                        v_sum_fees := v_sum_fees + v_fee + v_gst;
                        CALL lms.sp_post_ledger(p_loan_id, v_day, 'LATE_FEE', v_fee,
                                                'inst ' || v_inst.inst_no);
                        CALL lms.sp_post_ledger(p_loan_id, v_day, 'GST_ON_FEE', v_gst,
                                                'late fee inst ' || v_inst.inst_no);
                    END IF;

                    UPDATE lms.installment SET late_fee_charged = true
                     WHERE loan_id = p_loan_id
                       AND inst_no = v_inst.inst_no;
                END IF;

                -- penal interest stops once the loan is an NPA
                IF v_loan.asset_class <> 'NPA' THEN
                    v_penal_day := round(v_overdue * v_prod.penal_rate_pct / 100 / 365, 2);
                    v_loan.accrued_penal := v_loan.accrued_penal + v_penal_day;
                    v_sum_penal := v_sum_penal + v_penal_day;
                END IF;
            END IF;
        END LOOP;

        -- --------------------------------------------------------------
        -- 2f. Days past due and asset classification.
        --     DPD runs from the contractual due date of the oldest unpaid
        --     billed instalment.
        -- --------------------------------------------------------------
        SELECT min(due_date) INTO v_oldest
          FROM lms.installment
         WHERE loan_id = p_loan_id
           AND interest_due IS NOT NULL
           AND due_date < v_day
           AND ((principal_due - paid_principal)
              + (interest_due  - paid_interest)) > 0;

        v_dpd := CASE WHEN v_oldest IS NULL THEN 0 ELSE v_day - v_oldest END;

        v_class := CASE
                       WHEN v_dpd = 0                          THEN 'STANDARD'
                       WHEN v_dpd <= 30                        THEN 'SMA0'
                       WHEN v_dpd <= 60                        THEN 'SMA1'
                       WHEN v_dpd <= coalesce(v_prod.npa_dpd, 90) THEN 'SMA2'
                       ELSE 'NPA'
                   END;

        IF v_class = 'NPA' AND v_loan.asset_class <> 'NPA' THEN
            -- income already booked is reversed into suspense
            v_loan.suspense_interest :=
                v_loan.suspense_interest + v_loan.accrued_interest;
            CALL lms.sp_post_ledger(p_loan_id, v_day, 'NPA_REVERSAL',
                                    v_loan.accrued_interest, 'to suspense');
            v_loan.accrued_interest := 0;
        ELSIF v_class <> 'NPA' AND v_loan.asset_class = 'NPA' THEN
            v_loan.accrued_interest :=
                v_loan.accrued_interest + v_loan.suspense_interest;
            CALL lms.sp_post_ledger(p_loan_id, v_day, 'NPA_UPGRADE',
                                    v_loan.suspense_interest, 'from suspense');
            v_loan.suspense_interest := 0;
        END IF;

        IF v_class <> v_loan.asset_class THEN
            CALL lms.sp_post_ledger(p_loan_id, v_day, 'CLASS_CHANGE', v_dpd,
                                    v_loan.asset_class || ' to ' || v_class);
        END IF;

        v_loan.asset_class := v_class;
        v_loan.dpd := v_dpd;

        -- --------------------------------------------------------------
        -- 2g. Closure. Principal is nil and nothing else is owed. The
        --     interest accrued for the broken period is written off
        --     (LMS-2231).
        -- --------------------------------------------------------------
        IF v_loan.principal_outstanding = 0 THEN
            SELECT count(*) INTO v_unpaid
              FROM lms.installment
             WHERE loan_id = p_loan_id
               AND (interest_due IS NULL
                    OR ((principal_due - paid_principal)
                      + (interest_due  - paid_interest)) > 0);

            IF v_unpaid = 0
               AND v_loan.fees_outstanding = 0
               AND v_loan.accrued_penal = 0 THEN
                IF v_loan.accrued_interest > 0 THEN
                    CALL lms.sp_post_ledger(p_loan_id, v_day, 'WRITEOFF_ACCRUED',
                                            v_loan.accrued_interest,
                                            'broken period interest');
                    v_loan.accrued_interest := 0;
                END IF;
                v_loan.status := 'CLOSED';
                CALL lms.sp_post_ledger(p_loan_id, v_day, 'CLOSED', 0, 'loan closed');
                EXIT;
            END IF;
        END IF;
    END LOOP;

    -- ------------------------------------------------------------------
    -- 3. Save state and write the run summary
    -- ------------------------------------------------------------------
    UPDATE lms.loan
       SET last_run_date         = v_day,
           current_rate_pct      = v_prev_rate,
           principal_outstanding = v_loan.principal_outstanding,
           accrued_interest      = v_loan.accrued_interest,
           accrued_penal         = v_loan.accrued_penal,
           suspense_interest     = v_loan.suspense_interest,
           fees_outstanding      = v_loan.fees_outstanding,
           advance_balance       = v_loan.advance_balance,
           asset_class           = v_loan.asset_class,
           dpd                   = v_loan.dpd,
           status                = v_loan.status
     WHERE loan_id = p_loan_id;

    INSERT INTO lms.loan_run_summary
           (loan_id, as_of, days_processed, interest_accrued, penal_accrued,
            fees_charged, payments_applied, dpd, asset_class)
    VALUES (p_loan_id, p_as_of, v_days, v_sum_int, v_sum_penal,
            v_sum_fees, v_sum_paid, v_loan.dpd, v_loan.asset_class);

EXCEPTION
    WHEN OTHERS THEN
        INSERT INTO lms.loan_error_log (loan_id, as_of, sqlstate, message)
        VALUES (p_loan_id, p_as_of, SQLSTATE, SQLERRM);
        RAISE;
END;
$$;
