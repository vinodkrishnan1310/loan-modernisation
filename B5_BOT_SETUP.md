# B5 · Slack and Grok bots (module 2 demo)

Paste instructions from `bots/*.md` into Grok Bots. Fill **approver Slack names** in `programme_lead.md` before pasting the Lead.

## Prerequisites

- Git remote: `https://github.com/vinodkrishnan1310/loan-modernisation` (`modernisation-kit` is the repo root). `main` is up to date.
- Keys: `XAI_API_KEY`, `CURSOR_API_KEY`, GitHub token with push to the repo.
- Laptop: `docker compose up -d`, `bash deploy/reset_programme_demo.sh --api` before each bot rehearsal.

## Slack channels

| Channel | Purpose |
|---------|---------|
| `#loan-modernisation` | Main programme thread |
| `#gate-rules` | Gate 1 — `APPROVE RULES <module>` |
| `#gate-merge` | Gate 2 — `MERGED <pr>` |
| `#gate-release` | Gate 3 — `APPROVE RELEASE <commit>` |

Post approver lists in each gate channel description.

## Create five bots

| Bot | Type | Paste file | Vault keys |
|-----|------|------------|------------|
| Rule Extractor | Your bot | `bots/rule_extractor.md` | `XAI_API_KEY`, GitHub |
| Converter | Your bot | `bots/converter.md` | `CURSOR_API_KEY`, GitHub |
| Parity | Your bot | `bots/parity.md` | `XAI_API_KEY`, GitHub |
| Release | Your bot | `bots/release.md` | GitHub |
| Programme Lead | **Shared** bot | `bots/programme_lead.md` (after filling approvers) | Slack, GitHub |

Enable GitHub and shell/code tools so specialists can clone, run Python, commit, and push.

## Smoke tests (before live extract)

1. In `#loan-modernisation`: `@Programme Lead ping Parity with {"task":"ping"}` — expect a reply in-thread.
2. Attach `slack-drop/prepayment_quote_bundle.sql`, say `prepayment_quote`, @ Lead — Lead should save to `legacy/incoming/prepayment_quote_bundle.sql` on `mod/prepayment-quote` and call Rule Extractor.
3. Reply `ok` to a gate request — Lead should refuse. Reply exact `APPROVE RULES prepayment_quote` in `#gate-rules` as an approver — Lead should accept.

## Module 2 rehearsal order

```bash
bash deploy/reset_programme_demo.sh --api
git checkout main && git pull
git push origin --delete mod/prepayment-quote 2>/dev/null || true
```

In Slack (do **not** run `extract.py` locally):

1. Thread: attach prepayment bundle → `prepayment_quote` → @ Lead → Gate 1 in `#gate-rules`.
2. Same thread: attach notice bundle → `rate_reset_notice` → @ Lead → Gate 1.
3. Lead → Converter → one PR → Parity ×2 → Gate 2 merge on GitHub + `MERGED` in Slack.
4. Release packs → Gate 3 → tag → `deploy/watch_release.sh` on laptop.

See `MODULE2_DEMO_STEPS.md` for terminal fallbacks if a bot cannot reach GitHub.

## Customise before paste

In `bots/programme_lead.md` line **rules channel approvers** (and merge/release), replace `<slack names>` with `@handle` for each approver.
