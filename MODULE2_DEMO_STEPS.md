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
2. Today we run the **same programme** on two smaller procedures (~75-line Slack bundles each).
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

# Progress and retries print to stderr, e.g. [grok 08:15:01Z] attempt 1/3 started
# GROK_VERBOSE=0 silences them. GROK_TIMEOUT_SECONDS=180 overrides the 120s default.

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

### E. Slack bots (B5)

Follow **`B5_BOT_SETUP.md`**: create five Grok bots, paste `bots/*.md`, fill approver names in `programme_lead.md`, run smoke tests, then the Slack playbook below.

## Slack attachments for live extract

| Module | Attach in thread |
|--------|------------------|
| prepayment_quote | `slack-drop/prepayment_quote_bundle.sql` |
| rate_reset_notice | `slack-drop/rate_reset_notice_bundle.sql` |

Mention the **module name** in the thread so the Lead uses the right row in `MODULES`.

## Reset before rehearsal or live demo

Loans + programme artefacts (rules, evidence, `legacy/incoming`, `state/`):

```bash
bash deploy/reset_programme_demo.sh --api   # branches cleaned; strips conversion from main + push; --no-git / --no-replay-baseline optional
```

Save backups **before** reset if you want fallbacks:

```bash
mkdir -p presenter/rule-backups
cp rules/prepayment_quote.rules.json presenter/rule-backups/ 2>/dev/null || true
```

After reset, the Slack playbook starts with **no** `rules/*.json` on disk (same as first attach).

## Slack playbook (module 2) — what you do vs what bots do

### Rehearse notice extract (second proc) — terminal = Rule Extractor

After `reset_programme_demo.sh`, or on a branch with no notice rules yet:

```bash
source .venv/bin/activate && set -a && source .env && set +a
python tools/rule_extractor/extract.py \
  --file slack-drop/rate_reset_notice_bundle.sql \
  --out rules/rate_reset_notice.rules.json
```

### Rehearse Lead “file landed in repo” (optional once per module)

Simulates step 1 before the bot runs extract (you can do this **instead** of pointing `--file` at `slack-drop/`):

```bash
# branch from bundle name (see scripts/branch_from_bundle.py):
python3 scripts/branch_from_bundle.py rate_reset_notice_bundle.sql
git checkout -B mod/rate-reset-notice
cp slack-drop/rate_reset_notice_bundle.sql legacy/incoming/rate_reset_notice_bundle.sql
git add legacy/incoming/rate_reset_notice_bundle.sql
git commit -m "legacy: rate_reset_notice bundle from Slack"
# git push -u origin mod/prepayment-quote   # when bots need GitHub

python tools/rule_extractor/extract.py \
  --file legacy/incoming/rate_reset_notice_bundle.sql \
  --out rules/rate_reset_notice.rules.json
git add rules/rate_reset_notice.rules.json
git commit -m "rules: rate_reset_notice extract"
```

### Live demo (both procs, Slack-only narrative)

1. `bash deploy/reset_programme_demo.sh --api`
2. Thread: attach **prepayment** + `@Lead prepayment_quote` → Gate 1 approve quote
3. Same thread (or step 2): attach **notice** + `@Lead rate_reset_notice` → Gate 1 approve notice  
   *(Until Lead START is module-aware, you may run extract on the bot machine or Plan B: you run extract when Lead asks.)*
4. Lead → Converter (one `run_d_module2_conversion.md`) after **both** approvals
5. Parity ×2 → merge → release → tag → watcher

You already completed quote extract; for a **full replay**, reset then run **both** extracts again (or both Slack drops with bots).
