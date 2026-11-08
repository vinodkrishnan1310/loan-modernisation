# Second module: prepayment quote and rate reset notice

This repo already converts one legacy procedure (`lms.sp_loan_eod_process`) to Python with parity checks.
This file describes two more procedures that follow the same pattern. Read `AGENTS.md` and `.cursor/rules/` first. All rules there apply.

## What is being added

| Legacy procedure | Source | What it does | Python target |
|---|---|---|---|
| `lms.sp_prepayment_quote(loan_id, quote_date)` | `sql/06_sp_prepayment_quote.sql` | Payoff quote for one loan. Read only, no helper functions. | `service/prepayment_quote.py` |
| `lms.sp_rate_reset_notice(as_of, window_days)` | `sql/07_sp_rate_reset_notice.sql` | Posts `RESET_NOTICE` ledger rows for floating-rate loans about to reset. Runs over the whole loan book. | `service/rate_reset_notice.py` |

The quote procedure is self-contained. The notice procedure uses the helper functions in `sql/03_helpers.sql` (`fn_rate_for_day`, `fn_next_business_day`, `sp_post_ledger`). Both use only existing tables.
**No DDL.** Do not add tables, columns, indexes or functions.

## Files already in the repo (do not edit)

- `sql/06_*`, `sql/07_*`, `sql/08_demo_loans_module2.sql`: legacy procedures and demo loans 9003 and 9004.
- `golden/quote_cases.json` (18 cases) and `golden/notice_cases.json` (16 cases): inputs and outputs recorded from the real procedures.
- `golden/reference.json`: products, benchmark rates and holidays, shared with the first module.
- `parity/parity_module2.py`: the parity runner. Plain code, no LLM.
- `scripts/gen_cases_module2.py`: how the golden files were recorded.
- `fallback/`: earlier example conversions. Do not copy from them and do not edit them.

## What to build

### 1. `service/prepayment_quote.py`

```python
def quote(reference: dict, inputs: dict) -> dict
```
- `inputs = {"loan": {...}, "quote_date": "YYYY-MM-DD"}`. The loan dict has the columns of `lms.loan` as strings or null.
- Success returns `{"result": {"o_principal", "o_interest", "o_penal", "o_fees", "o_prepay_fee", "o_gst", "o_total": Decimal, "o_valid_until": date}, "ledger": [row]}`.
- `ledger` is always an empty list for the quote.
- When the procedure raises, return `{"error": "<first line of the message>", "ledger": []}`. The message text is part of the behaviour.

### 2. `service/rate_reset_notice.py`

```python
def notices(reference: dict, inputs: dict) -> dict
```
- `inputs = {"loans": [loan dicts, the whole book], "as_of": str, "window_days": int, "prior_ledger": [ledger rows already present]}`.
- Success returns `{"posted": int, "skipped": int, "ledger": [new rows]}`. A ledger row is `{"loan_id": str, "entry_date": str, "entry_type": "RESET_NOTICE", "amount": Decimal, "note": str}`. Error returns `{"error": str, "ledger": [...]}` like above.
- The procedure raises in the middle of its loop. Decide from the golden cases what must be in `ledger` when that happens.

### 3. Database adapters in `api/engine_service.py`

Add `run_prepayment_quote(conn, loan_id, quote_date)` and `run_rate_reset_notice(conn, as_of, window_days)`.
Load rows, call the pure functions, write ledger rows with the existing helper SQL pattern, and return the same keys the legacy path returns:
- quote: a dict with `o_principal, o_interest, o_penal, o_fees, o_prepay_fee, o_gst, o_total, o_valid_until`
- notice: a dict with `p_posted, p_skipped`
When the pure function returns an `error`, raise `psycopg.errors.RaiseException` with that text so the API returns 422 as before.
`api/main.py` already imports these two names when `ENGINE=service`. Do not edit `api/main.py`.

### 4. `parity/classify.py`

Add a `--golden` option (default `golden/golden_master.json`) so the classifier can read the two new golden files. The report format from `parity_module2.py` has the same `case_id`, `status`, `diffs` shape as the first module.

## API (already added, contract updated by a person)

| Method and path | Body | Notes |
|---|---|---|
| `POST /loans/{loan_id}/prepayment-quote` | `{"quote_date": "2024-06-15"}` | Read only. |
| `POST /rate-reset-notices` | `{"as_of": "2024-09-25", "window_days": 30}` | Runs over every active floating loan. |

`api/contract/openapi.json` was regenerated once, as an approved additive change, before this work. It is the contract now. `pytest api/tests` must keep passing and you must not regenerate the file.

## How to check your work

```bash
pytest
python parity/parity_module2.py --proc quote
python parity/parity_module2.py --proc notice
```
Each prints one line per case and a summary such as `17 of 18 cases match`. The reports are written to `evidence/parity_quote.json` and `evidence/parity_notice.json`.
Do not stop at the first failing case. For each difference you cannot explain, add it to the pull request under Open questions with the case id.
Python and Postgres can behave differently even where the code looks the same, so the parity run decides, not your reading of the SQL.

## Pull request

- One pull request for both procedures. Title: `[conversion] prepayment quote and rate reset notice`.
- Headings: What changed, Tests run, Parity result, Open questions. Put both parity summary lines under Parity result.
- Tests use recorded Grok responses only. Any classifier change needs a recorded fixture under `parity/tests/recorded/`.

## Applying the database scripts to an existing database

New files in `sql/` run only when the database volume is first created. On an existing stack:
```bash
docker compose exec -T db psql -U postgres -d loandb < sql/06_sp_prepayment_quote.sql
docker compose exec -T db psql -U postgres -d loandb < sql/07_sp_rate_reset_notice.sql
bash deploy/reset_demo.sh        # also creates demo loans 9003 and 9004
```
