---
name: build-grok-tool
description: Build the two Grok-powered tools in this repo, the rule extractor and the mismatch classifier. Use when asked to build or change either tool.
---
Follow `.cursor/rules/grok-calls.mdc` for every Grok call.

## Rule extractor: `tools/rule_extractor/extract.py`
Usage: `python tools/rule_extractor/extract.py --file legacy/incoming/loan_eod_bundle.sql --out rules/loan_eod.rules.json`
1. Read the file. Prefix every line with its number and a pipe (`  42| text`) before sending it, so citations are exact.
2. System prompt: `prompts/extractor_system.md`. Schema: `schemas/rules.schema.json`.
3. Validate the result. Then check every cited line range in plain code: the lines must exist and not all be blank.
   Record each failure in the output under `citation_failures` (this is a separate key added by the tool, not by Grok).
4. Write the JSON to `--out`. Print: number of rules, contradictions, open questions, citation failures.
5. Exit code 0 when all citations are valid, 2 when any are not. Never edit Grok's text to make a citation pass.
6. Tests: a recorded response in `tools/rule_extractor/tests/recorded/`, a test for the citation checker with one bad citation, and a test for exit codes.

## Mismatch classifier: `parity/classify.py`
Usage: `python parity/classify.py --report evidence/parity_report.json --rules rules/loan_eod.rules.json --out evidence/classified.json`
1. Read the parity report. For every case with status MISMATCH, build one record per distinct difference,
   shaped like `schemas/examples/example_mismatch_input.json`: the case, the product, the first divergence,
   a few legacy ledger entries before it, other differences, and the rules most relevant to it.
2. System prompt: `prompts/classifier_system.md`, with the label list from `parity/labels.json` inserted.
   Schema: `schemas/mismatch.schema.json`.
3. Compute `needs_human` in plain code: confidence below 0.80, or label `real_defect` or `unknown`,
   or the Grok call failed. Never ask Grok for it.
4. Write one entry per record to `--out`: the record, Grok's answer (or the failure), and `needs_human`.
   Print a summary: counts per label and how many need a human.
5. Tests with recorded responses: one per label, one low-confidence case, one `GrokUnavailable` case.
   A test must assert that `schemas/mismatch.schema.json` enum equals the names in `parity/labels.json`.

Open a pull request titled `[tool] ...`.
