# Strategy A demo — module 2 via your terminal

Loan EOD stays the **story** (640-line proc, slow extract). **Live** Grok + Slack uses the two smaller procedures (`prepayment_quote`, `rate_reset_notice`). You run each automation command yourself so you can narrate it.

Technical detail: `README_MODULE2.md`. Presenter notes (local only): `presenter/ANSWER_KEY_module2.md`.

## Baseline (once after this merge)

```bash
cd modernisation-kit
docker compose up -d --build
bash deploy/apply_module2_sql.sh
bash deploy/reset_demo.sh          # loans 9001–9004
curl -s localhost:8000/health

# Contract + module-2 legacy smoke (needs venv + deps)
source .venv/bin/activate
pytest api/tests/test_contract.py
pytest api/tests/test_module2_live.py   # requires 06/07 applied + reset
```

Swagger:

- `POST /loans/9003/prepayment-quote` — `{"quote_date": "2024-06-15"}`
- `POST /rate-reset-notices` — `{"as_of": "2024-09-25", "window_days": 30}`

## What you say in the room

1. EOD is the hard legacy problem; full rule extract is batch/chunked (or pre-run).
2. Today we run the **same programme** on two smaller procedures (~260 lines each).
3. Both share one conversion PR; parity runs per module.

## Terminal script order (no bots until B5)

### A. Loan EOD rules (offline / optional)

Pre-generate or use a saved file; do **not** block the demo on a 641-line live Grok call.

```bash
# optional, slow:
# python tools/rule_extractor/extract.py --file slack-drop/loan_eod_bundle.sql --out rules/loan_eod.rules.json
```

### B. Module 2 — rule extract (live demo)

```bash
source .venv/bin/activate && set -a && source .env && set +a

python tools/rule_extractor/extract.py \
  --file slack-drop/prepayment_quote_bundle.sql \
  --out rules/prepayment_quote.rules.json

python tools/rule_extractor/extract.py \
  --file slack-drop/rate_reset_notice_bundle.sql \
  --out rules/rate_reset_notice.rules.json
```

Gate 1 (human): approve rules in Slack, e.g. `APPROVE RULES prepayment_quote` / notice per your Lead config.

### C. Conversion (Cursor cloud agent)

After both rules files are on the branch you use for conversion:

```bash
export CURSOR_API_KEY='...'   # Cursor API key, not xAI
python3 scripts/cursor_run.py \
  --repo https://github.com/vinodkrishnan1310/loan-modernisation \
  --ref main \
  --prompt-file prompts/cursor/run_d_module2_conversion.md
```

### D. Parity (on PR branch)

```bash
pytest -q
python parity/parity_module2.py --proc quote --out evidence/parity_quote.json
python parity/parity_module2.py --proc notice --out evidence/parity_notice.json
# classify once --golden support lands on the conversion PR branch:
# python parity/classify.py --report evidence/parity_quote.json --rules rules/prepayment_quote.rules.json --golden golden/quote_cases.json --out evidence/classified_quote.json
```

Fallback without service code:

```bash
python parity/parity_module2.py --proc quote --module fallback/second-pass/prepayment_quote.py
python parity/parity_module2.py --proc notice --module fallback/second-pass/rate_reset_notice.py
```

### E. Slack bots

Paste updated `bots/*.md` (especially `programme_lead.md`, `converter.md`, `parity.md`). Set `REPO` and approver names.

## Slack attachments for live extract

| Module | Attach in thread |
|--------|------------------|
| prepayment_quote | `slack-drop/prepayment_quote_bundle.sql` |
| rate_reset_notice | `slack-drop/rate_reset_notice_bundle.sql` |

Mention the **module name** in the thread so the Lead uses the right row in `MODULES`.
