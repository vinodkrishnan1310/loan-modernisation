# Release evidence pack: rate_reset_notice

Commit `5c4c91c36ba65ecc211bfdf5f163cc774d5541f6`, PR #18. Built from files in this repo; no figure below was typed by hand.

## 1. Result

- Recorded cases replayed: **16**
- Identical to legacy: **16**
- Different: **0**
- Unit tests: passed (Parity run on PR 18, head 8af4740). API contract test: TO CONFIRM.

## 2. Every difference

None. All recorded cases match.

## 3. Open questions from the rules

- Confirm that missing benchmark rates are intentionally treated as 0 rather than raising an error or using a prior rate.
- Confirm that the month-based reset calculation (integer division then add reset_months) matches intended cycle logic for all reset_months values.
- Confirm that duplicate suppression relies solely on the note text pattern rather than a dedicated flag or date column.

## 4. Approval trail

| Gate | Approved by | When | Reference |
|---|---|---|---|
| rules | Vinod Krishnan | 1794365782.622859 |  |

## 5. Rollback plan

The legacy stored procedure stays in the database and the API keeps the same contract, so rollback is a change of engine, not a restore.

1. Trigger: **TO CONFIRM** (for example a new parity difference in shadow checks, or an error rate above a threshold the release manager sets).
2. Action: `bash deploy/release.sh rollback "<reason>"`. The laptop watcher redeploys the API with `ENGINE=legacy` and posts the result.
3. Expected time: **TO CONFIRM** (measure in rehearsal).
4. After rollback: reconcile any rows the service engine wrote during the window. Owner: **TO CONFIRM**.
5. Keep until cutover is signed off: the stored procedure, the `golden/` data, and the previous release tag.

## 6. Decision requested

Release manager: reply in the Slack thread with `APPROVE RELEASE <commit>` to cut over, or `REJECT RELEASE: <reason>`.
