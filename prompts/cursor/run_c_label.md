Parity case C05 was classified `unknown` with low confidence: a floating-rate reset takes effect on a different day in the new code than in the legacy code.

Use the skill `add-mismatch-label` to add a label named `date_boundary`.
Definition: "A rate or event takes effect on a different day because dates are counted or rolled differently, for example at month ends."
Example: "A rate reset lands one day earlier in the new code."

Re-run the classifier on `evidence/parity_report.json` and put the counts per label, before and after, in the pull request description.
Open a pull request titled `[label] add date_boundary`.
