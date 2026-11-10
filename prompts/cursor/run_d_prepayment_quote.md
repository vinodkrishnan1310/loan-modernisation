Convert only the stored procedure `lms.sp_prepayment_quote` to Python, following `README_MODULE2.md` and the skill `convert-stored-proc`.

Legacy source: `sql/06_sp_prepayment_quote.sql` (self-contained; no helper functions).
Approved rules, if present: `rules/prepayment_quote.rules.json`.

Do in this order:
1. Pure function in `service/prepayment_quote.py` (`quote(reference, inputs)`). Return shape in README_MODULE2.md.
2. Database adapter `run_prepayment_quote` in `api/engine_service.py` (wire existing HTTP route if not already).
3. If needed for parity, extend `parity/classify.py` with `--golden` for `golden/quote_cases.json`.

Do not implement `rate_reset_notice` in this PR. Do not change the HTTP contract, golden data, SQL, or `fallback/`.
Run `pytest`, then `python parity/parity_module2.py --proc quote`, and put the summary in the pull request description.
Open one pull request titled `[conversion] prepayment quote`.
Open the pull request with base branch equal to the branch you started from (not main). If you have no changes to commit, do not open a pull request; say so.