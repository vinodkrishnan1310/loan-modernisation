Build the two Grok-powered tools described in the skill `build-grok-tool`: the rule extractor (`tools/rule_extractor/extract.py`) and the mismatch classifier (`parity/classify.py`).

Follow every rule in `.cursor/rules`. Use the schemas in `schemas/`, the prompts in `prompts/`, and the labels in `parity/labels.json`.
Write the tests with recorded responses. Run `pytest`. Open a pull request titled `[tool] rule extractor and mismatch classifier`.
