# Loan modernisation kit

Legacy: Postgres PL/pgSQL loan end-of-day procedure (stand-in for Oracle). Target: Python service behind the same FastAPI contract.

Start the stack:   docker compose up -d --build        (db on 5433, api on 8000, /docs for the demo)
Switch engine:     ENGINE=service docker compose up -d --build api
Reset demo loans:  bash deploy/reset_demo.sh
Watch for tags:    bash deploy/watch_release.sh        (run from a separate clone)
Tag a release:     bash deploy/release.sh release "approved by <name>"
Contract test:     cd api && pytest tests
Parity:            python parity/parity.py --module fallback/second-pass/loan_eod.py --out evidence/parity_report.json

Folders: sql (legacy database), api, deploy, parity, schemas, prompts, bots (instructions to paste into each Bot),
.cursor (rules and skills the Cursor agent follows), fallback (pre-built conversions), golden (recorded legacy results),
slack-drop (the file to attach in Slack), presenter (never commit: answer key and example outputs).
Fill in .env from .env.example. All data is synthetic.
