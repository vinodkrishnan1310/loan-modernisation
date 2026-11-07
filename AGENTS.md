# Loan modernisation repo

Moves loan end-of-day logic from a Postgres stored procedure (`sql/`) to a Python service,
and proves the new code gives the same answers as the old.

## Layout
- `sql/`            legacy database objects. Read only.
- `legacy/incoming/` stored procedure text dropped in by the programme lead bot.
- `rules/`          business rules extracted from the legacy code (approved by a person before conversion).
- `golden/`         recorded legacy inputs and outputs. Read only.
- `parity/`         parity harness (`parity.py`, plain code), classifier (`classify.py`), labels and schema builder.
- `service/`        the converted engine (`loan_eod.py`, pure functions).
- `api/`            FastAPI app. `engine_service.py` is the adapter that loads from and saves to Postgres.
- `schemas/`        JSON schemas for every Grok call. `prompts/` holds the system prompts.
- `evidence/`       parity reports and release packs.
- `deploy/`         release scripts for the laptop. Do not edit.
- Second module (prepayment quote, rate reset notice): see `README_MODULE2.md`. Files: `sql/06*`, `sql/07*`, `golden/quote_cases.json`, `golden/notice_cases.json`, `parity/parity_module2.py`, `service/prepayment_quote.py`, `service/rate_reset_notice.py`.

## Commands
- All tests: `pytest`
- Contract test only: `pytest api/tests`
- Parity: `python parity/parity.py --module service/loan_eod.py`
- Parity for the second module: `python parity/parity_module2.py --proc quote` and `--proc notice`
- Classify differences: `python parity/classify.py --report evidence/parity_report.json --rules rules/loan_eod.rules.json`

## Rules of the road
See `.cursor/rules/`. In short: Decimal for money, no DDL, API contract unchanged, Grok reads and plain code decides,
tests use recorded Grok responses.
