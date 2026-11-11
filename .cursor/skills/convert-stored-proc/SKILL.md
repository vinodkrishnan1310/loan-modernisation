---
name: convert-stored-proc
description: Convert the legacy loan end-of-day stored procedure into the Python service and wire it into the API behind the ENGINE flag. Use when asked to convert or re-convert the procedure.
---
Inputs: the legacy code in `legacy/incoming/`, the approved rules in `rules/loan_eod.rules.json`,
the recorded cases in `golden/`, and the existing API in `api/`.

1. Read the legacy code and the approved rules. Where they disagree, the legacy code wins; list each case under Open questions.
2. Read one case from `golden/golden_master.json`. The shape of `legacy_output` is the shape your engine must return.
3. Write `service/loan_eod.py` with
   `process_loan(reference: dict, case_inputs: dict, state: dict | None = None) -> dict`.
   It is a pure function: no database, no clock, no environment. `state=None` means a new loan.
   Pass a state in to continue from where the previous run stopped, as the procedure does with its last run date.
4. Write `api/engine_service.py` with `run_eod(conn, loan_id, as_of)`. It
   - locks the loan row with `SELECT ... FOR UPDATE`,
   - loads the loan, instalments, unapplied payments, product, benchmark rates and holidays,
   - calls `process_loan` from the stored state,
   - writes the results back to the same tables inside the caller's transaction, appending ledger rows,
   - never creates or alters any database object.
5. Leave `api/main.py`, the response models and `api/contract/openapi.json` unchanged. The `ENGINE` flag already selects the engine.
6. Add tests: a pytest that replays every case in `golden/` through `process_loan`, and one test for the adapter with a fake connection.
7. Run `pytest`, then the parity command given in the task prompt. Put the summary line in the PR.
   Do not change `golden/` to make a case pass. Do not special-case a case id.
8. Open a pull request titled `[conversion] loan end-of-day service`.
