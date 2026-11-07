# Demo runbook — module 2 (single-proc programme)

End-to-end steps from **cold reset** through **deploy watcher**, using **Slack + Grok bots** and **SINGLE_MODULE** mode (one stored procedure per programme run). Repo root: this folder (`loan-modernisation` on GitHub).

**Narrative:** Loan EOD is the hard legacy story; **live** extract uses `prepayment_quote` then `rate_reset_notice` (~260 lines each). Optionally demo quote only in the room and pre-run notice.

---

## 0. One-time setup (before first demo)

### 0.1 Laptop stack

```bash
cd /path/to/modernisation-kit
cp .env.example .env
# Fill: XAI_API_KEY, CURSOR_API_KEY, GROK_MODEL, SLACK_WEBHOOK_URL (optional), ENGINE=legacy

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r api/requirements.txt

docker compose up -d --build
bash deploy/apply_module2_sql.sh   # once per db volume
```

Check:

```bash
curl -s localhost:8000/health
curl -s -X POST localhost:8000/loans/9003/prepayment-quote \
  -H 'Content-Type: application/json' -d '{"quote_date":"2024-06-15"}'
```

### 0.2 GitHub

- Remote: `https://github.com/vinodkrishnan1310/loan-modernisation`
- Push `main` with latest `bots/`, prompts, `deploy/reset_programme_demo.sh`
- **PAT** for bots: read/write repo, push branches, tags (Release)

### 0.3 Slack channels

| Channel | Role |
|---------|------|
| `#loan-modernisation` | Main programme threads |
| `#gate-rules` | Gate 1 — `APPROVE RULES <module_id>` |
| `#gate-merge` | Gate 2 — `MERGED <pr>` |
| `#gate-release` | Gate 3 — `APPROVE RELEASE <commit>` |

Approver (in `bots/programme_lead.md`): **@Vinod Krishnan** on all three gates.

### 0.4 Grok bots (B5)

Paste from `bots/*.md` into Grok. See `B5_BOT_SETUP.md` for keys.

| Bot | Keys / tools |
|-----|----------------|
| Rule Extractor | `XAI_API_KEY`, GitHub, shell |
| Converter | `CURSOR_API_KEY`, GitHub |
| Parity | `XAI_API_KEY`, GitHub |
| Release | GitHub (tags) |
| Programme Lead (shared) | Slack, GitHub |

**Handoffs:** Slack Lead cannot DM other bots. Use a **Grok group** (Lead + 4 specialists), or invite **Rule Extractor** to `#loan-modernisation`, or **you bridge** JSON to Rule Extractor in Grok.

### 0.5 Deploy watcher (optional, full deploy story)

Terminal 2 — leave running during demo:

```bash
cd /path/to/modernisation-kit
set -a && source .env && set +a
bash deploy/watch_release.sh
```

---

## 1. Branch naming (derived from bundle file)

| Attachment | `module_id` | Git branch |
|------------|-------------|------------|
| `prepayment_quote_bundle.sql` | `prepayment_quote` | `mod/prepayment-quote` |
| `rate_reset_notice_bundle.sql` | `rate_reset_notice` | `mod/rate-reset-notice` |
| `loan_eod_bundle.sql` | `loan_eod` | `mod/loan-eod` |

Check locally:

```bash
python3 scripts/branch_from_bundle.py prepayment_quote_bundle.sql
```

---

## 2. Full reset (start every rehearsal / live demo)

Wipes **DB loans**, **local** rules/evidence, and **all `mod/*` branches** on GitHub.

```bash
cd /path/to/modernisation-kit
# Optional backup:
mkdir -p presenter/rule-backups
cp rules/*.rules.json presenter/rule-backups/ 2>/dev/null || true

bash deploy/reset_programme_demo.sh --api
```

Skip git cleanup only: `bash deploy/reset_programme_demo.sh --api --no-git`

Confirm: `git branch -r` shows no `origin/mod/*`; API health OK on legacy engine.

---

## 3. Programme run A — prepayment quote (SINGLE_MODULE)

### 3.1 Slack — start extract

New thread in `#loan-modernisation`:

```text
@Programme Lead SINGLE_MODULE — prepayment_quote only.
```

Attach: `slack-drop/prepayment_quote_bundle.sql`

**Expect:** Lead pushes `legacy/incoming/prepayment_quote_bundle.sql` on `mod/prepayment-quote`, hands Rule Extractor `extract_rules`.

**Verify GitHub:**  
`https://github.com/vinodkrishnan1310/loan-modernisation/tree/mod/prepayment-quote`

**If Lead does not push** — manual bootstrap:

```bash
git checkout main && git pull
git checkout -B mod/prepayment-quote
mkdir -p legacy/incoming
cp slack-drop/prepayment_quote_bundle.sql legacy/incoming/
git add legacy/incoming/prepayment_quote_bundle.sql
git commit -m "legacy: prepayment quote bundle"
git push -u origin mod/prepayment-quote
```

**If Rule Extractor not reachable** — forward in Grok:

```json
{"task":"extract_rules","repo":"https://github.com/vinodkrishnan1310/loan-modernisation","branch":"mod/prepayment-quote","file":"legacy/incoming/prepayment_quote_bundle.sql","out":"rules/prepayment_quote.rules.json"}
```

### 3.2 Gate 1

Lead posts request in `#gate-rules`. Reply **in that channel** on the request message:

```text
APPROVE RULES prepayment_quote
```

### 3.3 Conversion

Lead → Converter. Handoff shape:

```json
{"task":"convert","repo":"https://github.com/vinodkrishnan1310/loan-modernisation","ref":"mod/prepayment-quote","rules":"rules/prepayment_quote.rules.json","prompt_file":"prompts/cursor/run_d_prepayment_quote.md"}
```

Wait for PR (title like `[conversion] prepayment quote`). Note **PR number**.

**Plan B (laptop):**

```bash
export CURSOR_API_KEY='...'
python3 scripts/cursor_run.py \
  --repo https://github.com/vinodkrishnan1310/loan-modernisation \
  --ref mod/prepayment-quote \
  --prompt-file prompts/cursor/run_d_prepayment_quote.md
```

### 3.4 Parity

Lead → Parity:

```json
{"task":"parity","repo":"https://github.com/vinodkrishnan1310/loan-modernisation","pr":<N>,"module":"prepayment_quote","rules":"rules/prepayment_quote.rules.json"}
```

### 3.5 Gate 2 — merge

1. Review PR on GitHub; **merge to `main`**.
2. In `#gate-merge`, reply on Lead’s message:

```text
MERGED <N>
```

### 3.6 Release

Lead → Release `build_pack` for `prepayment_quote`.

Gate 3 in `#gate-release`:

```text
APPROVE RELEASE <commit_sha_on_main>
```

Lead → Release `tag_release` → watcher deploys `ENGINE=service`, posts to Slack if webhook set.

### 3.7 Show cutover

```bash
curl -s localhost:8000/health
# Swagger: http://localhost:8000/docs
# POST /loans/9003/prepayment-quote with quote_date
```

---

## 4. Programme run B — rate reset notice (after quote merged)

Repeat **§2 reset** only if you want a **full replay** from empty git. Otherwise start from updated `main` without deleting `main` history.

New thread:

```text
@Programme Lead SINGLE_MODULE — rate_reset_notice only.
```

Attach: `slack-drop/rate_reset_notice_bundle.sql`

Branch: `mod/rate-reset-notice` (from filename).

Converter prompt: `prompts/cursor/run_d_rate_reset_notice.md`

Gates: `APPROVE RULES rate_reset_notice` → … → `MERGED` → `APPROVE RELEASE`.

Smoke:

```bash
curl -s -X POST localhost:8000/rate-reset-notices \
  -H 'Content-Type: application/json' -d '{"as_of":"2024-09-25","window_days":30}'
```

---

## 5. Combined two-proc PR (optional)

Only if you want **one** conversion PR for both modules. In Slack:

```text
@Programme Lead COMBINED_MODULE2
```

Extract + Gate 1 **both** modules on branch from **first** bundle filename; Converter uses `prompts/cursor/run_d_module2_conversion.md` with both rules paths.

---

## 6. Gate phrases cheat sheet

| Gate | Channel | Exact reply |
|------|---------|-------------|
| 1 | `#gate-rules` | `APPROVE RULES <module_id>` |
| Reject rules | `#gate-rules` | `REJECT RULES <module_id>: <reason>` |
| 2 | `#gate-merge` | `MERGED <pr number>` |
| Reject merge | `#gate-merge` | `REJECT MERGE: <reason>` |
| 3 | `#gate-release` | `APPROVE RELEASE <commit>` |

“ok” / thumbs-up **do not** count — only exact phrase, correct channel, approver listed in Lead config.

---

## 7. Plan B matrix (bot / network failure)

| Step | You run |
|------|---------|
| Push bundle | §3.1 manual git block |
| Extract | `python3 tools/rule_extractor/extract.py --file legacy/incoming/... --out rules/...` + commit/push |
| Convert | `scripts/cursor_run.py` (§3.3) |
| Parity | `pytest -q`; `python parity/parity_module2.py --proc quote\|notice --out evidence/parity_report.json` |
| Tag | `bash deploy/release.sh release "approved by Vinod in Slack"` |

Paste bot JSON results into Slack so Lead can continue.

---

## 8. Rollback story

In Slack via Lead → Release:

```bash
bash deploy/release.sh rollback "demo rollback"
```

Watcher sets `ENGINE=legacy` again.

---

## 9. Pre-demo checklist (5 min)

- [ ] `bash deploy/reset_programme_demo.sh --api` completed
- [ ] `main` on GitHub matches local bot paste
- [ ] Watcher running (if showing deploy)
- [ ] Grok Lead instructions = `bots/programme_lead.md`
- [ ] Rule Extractor reachable (group / channel / bridge)
- [ ] `.env` loaded for watcher webhook
- [ ] Fallback rules in `presenter/rule-backups/` if needed

---

## 10. Related docs

| File | Content |
|------|---------|
| `B5_BOT_SETUP.md` | Bot creation smoke tests |
| `MODULE2_DEMO_STEPS.md` | Strategy A terminal fallbacks |
| `README_MODULE2.md` | API / parity detail |
| `bots/programme_lead.md` | Lead behaviour (paste into Grok) |
