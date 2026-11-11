Convert the loan end-of-day stored procedure in `legacy/incoming/` to the Python service, using the skill `convert-stored-proc`.
The approved business rules are in `rules/loan_eod.rules.json`.

Follow every rule in `.cursor/rules`. Do not change the HTTP contract, the golden data or the database.
Run `pytest`, then `python parity/parity.py --module service/loan_eod.py`, and put the parity summary in the pull request description.
Open a pull request titled `[conversion] loan end-of-day service`.
Open the pull request with base branch equal to the branch you started from (not main). If you have no changes to commit, do not open a pull request; say so.
