---
name: add-mismatch-label
description: Add a new mismatch label to the parity classifier. Use when a difference has no suitable label or a person asks for a new one.
---
1. Add an entry to `parity/labels.json` with `name` (lower snake case), `definition` (one sentence) and `example` (one sentence).
2. Run `python parity/build_schema.py`. It rewrites the `label` enum in `schemas/mismatch.schema.json` from `labels.json`. Do not edit the schema by hand.
3. Add a recorded response for the new label under `parity/tests/recorded/` and a test that classifies a record with it.
4. Confirm the existing tests still pass, including the one that checks the schema enum equals `labels.json`.
5. Re-run the classifier on `evidence/parity_report.json` and put the before and after counts per label in the PR description.
6. Open a pull request titled `[label] add <name>`.
