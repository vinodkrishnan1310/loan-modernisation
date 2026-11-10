Convert only the stored procedure `lms.sp_rate_reset_notice` to Python, following `README_MODULE2.md` and the skill `convert-stored-proc`.

Legacy source: `sql/07_sp_rate_reset_notice.sql` (self-contained; no helper functions).
Approved rules, if present: `rules/rate_reset_notice.rules.json`.

Do in this order:
1. Pure function in `service/rate_reset_notice.py` (`notices(reference, inputs)`). Return shape in README_MODULE2.md.
2. Database adapter `run_rate_reset_notice` in `api/engine_service.py` (wire existing HTTP route if not already).
3. If needed for parity, extend `parity/classify.py` with `--golden` for `golden/notice_cases.json`.

Do not change prepayment quote code except shared wiring if required. Do not change the HTTP contract, golden data, SQL, or `fallback/`.
Run `pytest`, then `python parity/parity_module2.py --proc notice`, and put the summary in the pull request description.
Open one pull request titled `[conversion] rate reset notice`.


Open the pull request with base branch equal to the branch you started from (not main). If you have no changes to commit, do not open a pull request; say so.
