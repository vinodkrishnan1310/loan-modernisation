Convert the two stored procedures `lms.sp_prepayment_quote` and `lms.sp_rate_reset_notice` to Python, following `README_MODULE2.md` in the repo root and the skill `convert-stored-proc`.

Their legacy source is in `sql/06_sp_prepayment_quote.sql` and `sql/07_sp_rate_reset_notice.sql` (helpers in `sql/03_helpers.sql`).
Approved rules, if present: `rules/prepayment_quote.rules.json` and `rules/rate_reset_notice.rules.json`.

Do in this order, and run the tests after each step:
1. Pure functions in `service/prepayment_quote.py` (`quote(reference, inputs)`) and `service/rate_reset_notice.py` (`notices(reference, inputs)`). Return shapes are in README_MODULE2.md.
2. Database adapters `run_prepayment_quote` and `run_rate_reset_notice` in `api/engine_service.py`.
3. Extend `parity/classify.py` with a `--golden` option so it can read `golden/quote_cases.json` and `golden/notice_cases.json`.

Follow every rule in `.cursor/rules`. Do not change the HTTP contract, the golden data, the SQL or `fallback/`.
Run `pytest`, then `python parity/parity_module2.py --proc quote` and `--proc notice`, and put both summary lines in the pull request description.
Open one pull request titled `[conversion] prepayment quote and rate reset notice`.
