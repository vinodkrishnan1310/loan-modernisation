"""Classify parity mismatches.

Usage:
    python parity/classify.py --report evidence/parity_report.json \
        --rules rules/loan_eod.rules.json --out evidence/classified.json

Plain code builds one record per distinct difference and decides
`needs_human`. Grok only chooses a label and explains it. A difference needs
a human when confidence is below 0.80, the label is `real_defect` or
`unknown`, or the Grok call failed.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.grok_client import GrokUnavailable, OpenAIGrokClient

SCHEMA_PATH = ROOT / "schemas" / "mismatch.schema.json"
PROMPT_PATH = ROOT / "prompts" / "classifier_system.md"
LABELS_PATH = ROOT / "parity" / "labels.json"
GOLDEN_PATH = ROOT / "golden" / "golden_master.json"
REFERENCE_PATH = ROOT / "golden" / "reference.json"
DEFAULT_OUT = ROOT / "evidence" / "classified.json"

SCHEMA_NAME = "mismatch"
HUMAN_CONFIDENCE = Decimal("0.80")
HUMAN_LABELS = frozenset({"real_defect", "unknown"})
LEDGER_CONTEXT_ENTRIES = 3

# Hints are matched against the difference text. Longer hints are more specific.
# Interest figures also pull rate and calendar rules, because those rules change
# the amount. This only chooses which rules to show Grok.
KIND_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rate", ("rate_reset", "current_rate", "benchmark", "spread", "rate")),
    ("accrual", ("accrued_interest", "interest_accrued", "bill_interest", "accrual")),
    ("billing", ("bill_interest", "interest_due", "installment", "bill")),
    (
        "payment_waterfall",
        (
            "payments_applied",
            "principal_outstanding",
            "paid_",
            "pay_",
            "payment",
            "advance",
        ),
    ),
    ("fee", ("late_fee", "gst", "fee")),
    ("penal_interest", ("penal",)),
    ("calendar", ("day_count", "holiday", "business_day")),
    ("classification", ("asset_class", "suspense", "class", "dpd", "npa", "sma")),
    ("prepayment", ("prepay",)),
    ("closure", ("writeoff", "closure", "closed", "status")),
    ("error_handling", ("exception", "sqlstate", "error")),
)
INTEREST_MARKERS = (
    "bill_interest",
    "interest_due",
    "interest_accrued",
    "accrued_interest",
    "paid_interest",
    "pay_interest",
)
INTEREST_KINDS = ("accrual", "billing", "rate", "calendar")
MAX_RULES = 5


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(1, f"{self.prog}: error: {message}\n")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_schema() -> dict:
    return load_json(SCHEMA_PATH)


def format_labels(labels: list[dict]) -> str:
    lines = []
    for label in labels:
        lines.append(
            f"- {label['name']}: {label['definition']} Example: {label['example']}"
        )
    return "\n".join(lines)


def build_system_prompt() -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    if "{{LABELS}}" not in template:
        raise GrokUnavailable("classifier prompt is missing the {{LABELS}} marker")
    labels = load_json(LABELS_PATH)["labels"]
    return template.replace("{{LABELS}}", format_labels(labels))


def label_names() -> list[str]:
    return [item["name"] for item in load_json(LABELS_PATH)["labels"]]


def needs_human(answer: dict) -> bool:
    """Plain-code decision. Grok is not asked whether a person should look."""
    label = answer.get("label")
    if label in HUMAN_LABELS:
        return True
    try:
        confidence = Decimal(str(answer["confidence"]))
    except (KeyError, InvalidOperation, ValueError, TypeError):
        return True
    if not confidence.is_finite():
        return True
    return confidence < HUMAN_CONFIDENCE


def public_diff(diff: dict) -> dict:
    """The difference Grok sees. The ledger index stays in our own code."""
    return {
        "path": diff.get("path"),
        "legacy": diff.get("legacy"),
        "new": diff.get("new"),
    }


def distinct_differences(diffs: list[dict]) -> list[dict]:
    """One entry per unique path, legacy value, and new value."""
    chosen = []
    seen = set()
    for diff in diffs:
        if not isinstance(diff, dict) or "path" not in diff:
            continue
        rendered = json.dumps(public_diff(diff), sort_keys=True, default=str)
        if rendered in seen:
            continue
        seen.add(rendered)
        chosen.append(diff)
    return chosen


def legacy_context(case: dict | None, diffs: list[dict]) -> list:
    """A few legacy ledger entries before the first ledger divergence."""
    if not case:
        return []
    ledger = (case.get("legacy_output") or {}).get("ledger") or []
    first = next(
        (
            diff
            for diff in diffs
            if isinstance(diff, dict) and diff.get("path") == "ledger.first_divergence"
        ),
        None,
    )
    if first is None:
        return []
    index = first.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or index <= 0:
        return []
    start = max(0, index - LEDGER_CONTEXT_ENTRIES)
    return ledger[start:index]


def product_view(case: dict | None, products: dict) -> dict:
    loan = ((case or {}).get("inputs") or {}).get("loan") or {}
    code = loan.get("product_code")
    product = products.get(code) or {}
    return {
        "product_code": code,
        "day_count": product.get("day_count"),
        "rate_type": loan.get("rate_type"),
    }


def difference_text(diff: dict) -> str:
    return json.dumps(public_diff(diff), default=str).lower()


def kinds_for_diff(diff: dict) -> list[str]:
    """Rule kinds that bear on this difference, most specific first."""
    text = difference_text(diff)
    scored: list[tuple[int, int, str]] = []
    for order, (kind, hints) in enumerate(KIND_HINTS):
        hits = [hint for hint in hints if hint in text]
        if hits:
            scored.append((max(len(hint) for hint in hits), -order, kind))
    kinds = [kind for _score, _order, kind in sorted(scored, reverse=True)]
    if any(marker in text for marker in INTEREST_MARKERS):
        for kind in INTEREST_KINDS:
            if kind not in kinds:
                kinds.append(kind)
    return kinds


def relevant_rules(diff: dict, rules: list[dict], limit: int = MAX_RULES) -> list[dict]:
    """Rules to show Grok. At most `limit`, spread across the matching kinds."""
    kinds = kinds_for_diff(diff)
    buckets: dict[str, list[dict]] = {kind: [] for kind in kinds}
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        kind = rule.get("kind")
        if kind not in buckets:
            continue
        if not rule.get("id") or not rule.get("statement"):
            continue
        buckets[kind].append(rule)
    for kind in buckets:
        buckets[kind].sort(key=lambda rule: rule["id"])
    chosen = []
    while len(chosen) < limit and any(buckets.values()):
        for kind in kinds:
            bucket = buckets[kind]
            if bucket and len(chosen) < limit:
                rule = bucket.pop(0)
                chosen.append({"id": rule["id"], "statement": rule["statement"]})
    return chosen


def build_records(
    report: list,
    rules: list[dict],
    cases_by_id: dict,
    products_by_code: dict,
) -> list[dict]:
    """One record per distinct difference on each MISMATCH case."""
    records = []
    for case in report:
        if not isinstance(case, dict) or case.get("status") != "MISMATCH":
            continue
        diffs = case.get("diffs") or []
        distinct = distinct_differences(diffs)
        if not distinct:
            continue
        golden = cases_by_id.get(case.get("case_id"))
        context = legacy_context(golden, diffs)
        product = product_view(golden, products_by_code)
        shown = [public_diff(diff) for diff in distinct]
        for index, diff in enumerate(distinct):
            record = {
                "case_id": case.get("case_id"),
                "scenario": case.get("scenario"),
                "product": product,
                "diff": shown[index],
                "legacy_context": context,
                "other_diffs": shown[:index] + shown[index + 1 :],
                "relevant_rules": relevant_rules(diff, rules),
            }
            records.append(copy.deepcopy(record))
    return records


def classify_records(records: list[dict], client, schema: dict, system: str) -> list[dict]:
    """Classify each record. A failed call is recorded and the run continues."""
    entries = []
    for record in records:
        try:
            answer = client.complete(
                name=SCHEMA_NAME,
                schema=schema,
                system=system,
                user=json.dumps(record, indent=2, ensure_ascii=False, default=str),
            )
        except GrokUnavailable as exc:
            entries.append(
                {
                    "record": record,
                    "grok": None,
                    "failure": str(exc),
                    "needs_human": True,
                }
            )
            continue
        entries.append(
            {
                "record": record,
                "grok": answer,
                "failure": None,
                "needs_human": needs_human(answer),
            }
        )
    return entries


def summary_lines(entries: list[dict]) -> list[str]:
    counts = {name: 0 for name in label_names()}
    failed = 0
    human = 0
    extras: dict[str, int] = {}
    for entry in entries:
        if entry.get("needs_human"):
            human += 1
        answer = entry.get("grok")
        if not answer:
            failed += 1
            continue
        label = answer.get("label")
        if label in counts:
            counts[label] += 1
        else:
            extras[label] = extras.get(label, 0) + 1
    lines = [f"{name}: {count}" for name, count in counts.items()]
    lines.extend(f"{name}: {count}" for name, count in sorted(extras.items()))
    lines.append(f"grok_failed: {failed}")
    lines.append(f"needs_human: {human}")
    return lines


def print_summary(entries: list[dict]) -> None:
    for line in summary_lines(entries):
        print(line)


def _cases_by_id(golden: list) -> dict:
    return {
        case.get("case_id"): case
        for case in golden
        if isinstance(case, dict) and case.get("case_id")
    }


def _products_by_code(reference: dict) -> dict:
    products = reference.get("products") or []
    return {
        product.get("product_code"): product
        for product in products
        if isinstance(product, dict) and product.get("product_code")
    }


def main(argv: list[str] | None = None, client=None) -> int:
    parser = _Parser(description="Classify differences in a parity report.")
    parser.add_argument("--report", required=True, help="Parity report JSON")
    parser.add_argument("--rules", required=True, help="Extracted rules JSON")
    parser.add_argument(
        "--golden",
        default=str(GOLDEN_PATH),
        help="Golden cases JSON (e.g. golden/notice_cases.json)",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Classified output JSON")
    args = parser.parse_args(argv)

    report_path = Path(args.report)
    rules_path = Path(args.rules)
    if not report_path.is_file():
        print(f"report not found: {report_path}", file=sys.stderr)
        return 1
    if not rules_path.is_file():
        print(f"rules not found: {rules_path}", file=sys.stderr)
        return 1

    report = load_json(report_path)
    rules_document = load_json(rules_path)
    if not isinstance(report, list):
        print("parity report must be a list of cases", file=sys.stderr)
        return 1
    if not isinstance(rules_document, dict) or not isinstance(rules_document.get("rules"), list):
        print("rules file must be an object with a rules array", file=sys.stderr)
        return 1

    if client is None:
        try:
            client = OpenAIGrokClient.from_env()
        except GrokUnavailable as exc:
            print(f"Grok unavailable: {exc}", file=sys.stderr)
            return 1

    golden_path = Path(args.golden)
    if not golden_path.is_file():
        print(f"golden cases not found: {golden_path}", file=sys.stderr)
        return 1
    if not REFERENCE_PATH.is_file():
        print(f"reference data not found: {REFERENCE_PATH}", file=sys.stderr)
        return 1
    cases = _cases_by_id(load_json(golden_path))
    products = _products_by_code(load_json(REFERENCE_PATH))
    records = build_records(report, rules_document["rules"], cases, products)
    try:
        system = build_system_prompt()
        entries = classify_records(records, client, load_schema(), system)
    except GrokUnavailable as exc:
        print(f"Grok unavailable: {exc}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print_summary(entries)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
