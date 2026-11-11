---
name: convert-stored-proc
description: Convert one legacy Postgres stored procedure into a pure Python function plus a thin database adapter, behind the ENGINE flag. Use when a task prompt asks to convert or re-convert a stored procedure.
---
Inputs: the legacy procedure (in `sql/` or `legacy/incoming/`), the approved rules in `rules/`, the recorded
cases in `golden/`, and the existing API in `api/`. The task prompt names the exact files, function names and PR title.

1. Read the legacy code and the approved rules. Where they disagree, the legacy code wins; list each case under Open questions.
2. Read one recorded case for the module (the task prompt names the golden file). The shape of `legacy_output` is the shape your function must return.
3. Write the pure function in `service/`, with the name and signature the task prompt gives.
   It is a pure function: no database, no clock, no environment.
   Where the procedure carries state between runs (for example a last run date), take it as a parameter; `state=None` means a new run.
4. Write the adapter in `api/engine_service.py`, with the name the task prompt gives. It
   - loads what the procedure reads (loans, instalments, payments, product, benchmark rates, holidays, prior ledger rows),
   - takes row locks (`SELECT ... FOR UPDATE`) only where the procedure does,
   - calls the pure function,
   - writes results back to the same tables inside the caller's transaction only if the procedure writes, appending ledger rows,
   - never creates or alters any database object.
5. Leave `api/main.py`, the response models and `api/contract/openapi.json` unchanged. The `ENGINE` flag already selects the engine.
6. Add tests: a pytest that replays every recorded case for the module through the pure function, and one test for the adapter with a fake connection.
7. Run `pytest`, then the parity command given in the task prompt. Put the summary line in the PR.
   Do not change `golden/` to make a case pass. Do not special-case a case id.
8. Open one pull request with the title given in the task prompt.
