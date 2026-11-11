#!/usr/bin/env python3
"""Build the release evidence pack (Markdown) from files that already exist. Plain code: every number comes from a file.

  python3 scripts/build_release_pack.py --report evidence/parity_report.json \
      [--classified evidence/classified.json] [--rules rules/loan_eod.rules.json] \
      [--approvals state/approvals.json] --pr 12 --commit abc1234 \
      --unit-tests passed --contract-test passed --out evidence/release_pack.md

approvals.json is a list: [{"gate": "rules", "by": "name", "at": "2026-11-07T10:02Z", "ref": "slack thread link"}, ...]
Anything the files cannot tell us is written as "TO CONFIRM" for the release manager.
"""
import argparse, json, pathlib, sys

ap = argparse.ArgumentParser()
ap.add_argument("--report", required=True)
ap.add_argument("--classified")
ap.add_argument("--rules")
ap.add_argument("--approvals")
ap.add_argument("--pr", default="TO CONFIRM")
ap.add_argument("--commit", default="TO CONFIRM")
ap.add_argument("--unit-tests", default="TO CONFIRM")
ap.add_argument("--contract-test", default="TO CONFIRM")
ap.add_argument("--module", default="loan end-of-day")
ap.add_argument("--out", required=True)
a = ap.parse_args()

def load(p):
    return json.load(open(p)) if p and pathlib.Path(p).exists() else None

report = load(a.report)
if report is None:
    sys.exit(f"parity report not found: {a.report}")
classified = load(a.classified) or []
rules = load(a.rules)
approvals = load(a.approvals) or []

total = len(report)
matched = [c for c in report if c["status"] == "MATCH"]
bad = [c for c in report if c["status"] != "MATCH"]

# index classifications by case
by_case = {}
for item in classified:
    rec = item.get("record", {})
    by_case.setdefault(rec.get("case_id"), []).append(item)

L = []
L.append(f"# Release evidence pack: {a.module}\n")
L.append(f"Commit `{a.commit}`, PR #{a.pr}. Built from files in this repo; no figure below was typed by hand.\n")
L.append("## 1. Result\n")
L.append(f"- Recorded cases replayed: **{total}**")
L.append(f"- Identical to legacy: **{len(matched)}**")
L.append(f"- Different: **{len(bad)}**")
L.append(f"- Unit tests: {a.unit_tests}. API contract test: {a.contract_test}.\n")

L.append("## 2. Every difference\n")
if not bad:
    L.append("None. All recorded cases match.\n")
else:
    L.append("| Case | Scenario | Differences | Cause (Grok label) | Confidence | Needs a person |")
    L.append("|---|---|---|---|---|---|")
    for c in bad:
        items = by_case.get(c["case_id"], [])
        if items:
            labels = ", ".join(sorted({(i.get("grok") or {}).get("label", "grok failed") for i in items}))
            confs = [(i.get("grok") or {}).get("confidence") for i in items if i.get("grok")]
            conf = f"{min(confs):.2f}" if confs else "n/a"
            human = "yes" if any(i.get("needs_human") for i in items) else "no"
        else:
            labels, conf, human = "not classified", "n/a", "yes"
        L.append(f"| {c['case_id']} | {c['scenario']} | {len(c['diffs'])} | {labels} | {conf} | {human} |")
    L.append("")
    open_cases = [c["case_id"] for c in bad if not by_case.get(c["case_id"]) or any(i.get("needs_human") for i in by_case[c["case_id"]])]
    L.append(f"Cases still needing a person's decision: **{', '.join(open_cases) if open_cases else 'none'}**.\n")

L.append("## 3. Open questions from the rules\n")
if rules and (rules.get("open_questions") or rules.get("contradictions")):
    for q in rules.get("open_questions", []):
        L.append(f"- {q}")
    for c in rules.get("contradictions", []):
        L.append(f"- Comment and code disagree: {c['description']}")
    L.append("")
else:
    L.append("None recorded.\n")

L.append("## 4. Approval trail\n")
if approvals:
    L.append("| Gate | Approved by | When | Reference |")
    L.append("|---|---|---|---|")
    def pick(item, *keys):
        # The Programme Lead writes approver / ts / link; older records use by / at / ref. Accept both.
        for k in keys:
            if item.get(k) not in (None, ""):
                return str(item[k])
        return ""

    for x in approvals:
        L.append(f"| {pick(x, 'gate')} | {pick(x, 'approver', 'by')} | {pick(x, 'ts', 'at')} | {pick(x, 'link', 'ref')} |")
    L.append("")
else:
    L.append("No approvals recorded yet.\n")

L.append("## 5. Rollback plan\n")
L.append("The legacy stored procedure stays in the database and the API keeps the same contract, so rollback is a change of engine, not a restore.\n")
L.append("1. Trigger: **TO CONFIRM** (for example a new parity difference in shadow checks, or an error rate above a threshold the release manager sets).")
L.append('2. Action: `bash deploy/release.sh rollback "<reason>"`. The laptop watcher redeploys the API with `ENGINE=legacy` and posts the result.')
L.append("3. Expected time: **TO CONFIRM** (measure in rehearsal).")
L.append("4. After rollback: reconcile any rows the service engine wrote during the window. Owner: **TO CONFIRM**.")
L.append("5. Keep until cutover is signed off: the stored procedure, the `golden/` data, and the previous release tag.\n")

L.append("## 6. Decision requested\n")
L.append("Release manager: reply in the Slack thread with `APPROVE RELEASE <commit>` to cut over, or `REJECT RELEASE: <reason>`.")
pathlib.Path(a.out).write_text("\n".join(L) + "\n")
print(f"wrote {a.out}: {total} cases, {len(matched)} match, {len(bad)} differ")
