"""Mismatch classifier tests. Recorded responses stand in for Grok. No network.

The live test is the one exception: it calls Grok only when XAI_API_KEY is set.
"""

import json
import os
from pathlib import Path

import jsonschema
import pytest

from parity import classify
from tools.grok_client import GrokUnavailable, RecordedResponseClient

ROOT = Path(__file__).resolve().parents[2]
RECORDED = Path(__file__).resolve().parent / "recorded"
LABELS = [
    "rounding",
    "day_count_convention",
    "null_handling",
    "real_defect",
    "unknown",
]


class QueueClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.users = []

    def complete(self, *, name, schema, system, user):
        self.users.append(user)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return RecordedResponseClient(item).complete(
            name=name, schema=schema, system=system, user=user
        )


def _load(name):
    return json.loads((RECORDED / name).read_text(encoding="utf-8"))


def _schema():
    return json.loads((ROOT / "schemas" / "mismatch.schema.json").read_text(encoding="utf-8"))


def _sample_record(case_id="C16"):
    return {
        "case_id": case_id,
        "scenario": "flat_fee_only_product",
        "product": {"product_code": "AL-ST", "day_count": "ACT360", "rate_type": "FIXED"},
        "diff": {
            "path": "summary.fees_charged",
            "legacy": "250.00",
            "new": "0.00",
        },
        "legacy_context": [],
        "other_diffs": [],
        "relevant_rules": [{"id": "RULE-01", "statement": "A flat fee is charged once."}],
    }


def test_schema_enum_matches_labels():
    names = [item["name"] for item in json.loads((ROOT / "parity" / "labels.json").read_text())["labels"]]
    schema = _schema()
    assert schema["properties"]["label"]["enum"] == names
    assert names == LABELS


def test_system_prompt_inserts_labels_from_file():
    raw = (ROOT / "prompts" / "classifier_system.md").read_text(encoding="utf-8")
    labels = json.loads((ROOT / "parity" / "labels.json").read_text(encoding="utf-8"))["labels"]
    system = classify.build_system_prompt()
    assert system == raw.replace("{{LABELS}}", classify.format_labels(labels))
    assert "{{LABELS}}" not in system
    for label in labels:
        assert label["name"] in system
        assert label["definition"] in system


@pytest.mark.parametrize("label", LABELS)
def test_recorded_label(label):
    payload = _load(f"{label}.json")
    schema = _schema()
    jsonschema.validate(payload, schema)
    assert payload["confidence"] >= 0.80
    entries = classify.classify_records(
        [_sample_record()],
        RecordedResponseClient(payload),
        schema,
        system="unused",
    )
    assert entries[0]["grok"]["label"] == label
    assert entries[0]["failure"] is None
    assert entries[0]["needs_human"] is (label in {"real_defect", "unknown"})


def test_low_confidence_recorded_response_needs_human():
    payload = _load("low_confidence.json")
    schema = _schema()
    jsonschema.validate(payload, schema)
    assert payload["label"] == "rounding"
    assert payload["confidence"] < 0.80
    entries = classify.classify_records(
        [_sample_record()],
        RecordedResponseClient(payload),
        schema,
        system="unused",
    )
    assert entries[0]["grok"]["label"] == "rounding"
    assert entries[0]["needs_human"] is True


def test_grok_unavailable_sends_the_item_to_a_human_and_continues():
    schema = _schema()
    rounding = _load("rounding.json")
    client = QueueClient([GrokUnavailable("timeout"), rounding])
    entries = classify.classify_records(
        [_sample_record("C01"), _sample_record("C02")],
        client,
        schema,
        system="unused",
    )
    assert entries[0]["grok"] is None
    assert entries[0]["needs_human"] is True
    assert "timeout" in entries[0]["failure"]
    assert "label" not in entries[0]
    assert entries[1]["grok"]["label"] == "rounding"
    assert entries[1]["needs_human"] is False
    assert "needs_human" not in client.users[0]


def test_confidence_threshold_is_below_0_80():
    assert classify.needs_human({"label": "rounding", "confidence": json.loads("0.80")}) is False
    assert classify.needs_human({"label": "rounding", "confidence": json.loads("0.79")}) is True
    assert classify.needs_human({"label": "null_handling", "confidence": json.loads("0.80")}) is False
    assert classify.needs_human({"label": "real_defect", "confidence": json.loads("0.99")}) is True
    assert classify.needs_human({"label": "unknown", "confidence": json.loads("1")}) is True
    assert classify.needs_human({"label": "rounding"}) is True


def test_build_records_one_per_distinct_difference():
    report = [
        {"case_id": "C01", "scenario": "on_time", "status": "MATCH", "diffs": []},
        {
            "case_id": "C09",
            "scenario": "npa",
            "status": "ERROR",
            "diffs": [{"path": "exception", "legacy": None, "new": "Error"}],
        },
        {
            "case_id": "C16",
            "scenario": "flat_fee_only_product",
            "status": "MISMATCH",
            "diffs": [
                {"path": "summary.fees_charged", "legacy": "250.00", "new": "0.00"},
                {"path": "summary.fees_charged", "legacy": "250.00", "new": "0.00"},
                {
                    "path": "ledger.first_divergence",
                    "index": 4,
                    "legacy": {"entry_type": "LATE_FEE", "amount": "250.00"},
                    "new": {"entry_type": "LATE_FEE", "amount": "0.00"},
                },
                {"path": "loan.current_rate_pct", "legacy": "8.5000", "new": "8.4900"},
            ],
        },
    ]
    rules = [
        {"id": "RULE-01", "kind": "fee", "statement": "A flat fee is charged once."},
        {"id": "RULE-02", "kind": "penal_interest", "statement": "Penal interest stops at NPA."},
        {"id": "RULE-03", "kind": "rate", "statement": "The fixed rate is the rate on the loan."},
        {"id": "RULE-04", "kind": "accrual", "statement": "Interest accrues each day."},
        {"id": "RULE-05", "kind": "calendar", "statement": "A year has 365 days for this product."},
        {"id": "RULE-09", "kind": "fee", "statement": "GST is added to the fee."},
    ]
    ledger = [{"entry_type": f"E{i}"} for i in range(6)]
    cases = {
        "C16": {
            "case_id": "C16",
            "inputs": {"loan": {"product_code": "AL-ST", "rate_type": "FIXED"}},
            "legacy_output": {"ledger": ledger},
        }
    }
    products = {"AL-ST": {"product_code": "AL-ST", "day_count": "ACT360"}}
    records = classify.build_records(report, rules, cases, products)

    assert [record["diff"]["path"] for record in records] == [
        "summary.fees_charged",
        "ledger.first_divergence",
        "loan.current_rate_pct",
    ]
    fee = records[0]
    assert set(fee) == {
        "case_id",
        "scenario",
        "product",
        "diff",
        "legacy_context",
        "other_diffs",
        "relevant_rules",
    }
    assert fee["product"] == {
        "product_code": "AL-ST",
        "day_count": "ACT360",
        "rate_type": "FIXED",
    }
    assert "index" not in fee["diff"]
    assert fee["legacy_context"] == ledger[1:4]
    assert [item["path"] for item in fee["other_diffs"]] == [
        "ledger.first_divergence",
        "loan.current_rate_pct",
    ]
    assert fee["relevant_rules"] == [
        {"id": "RULE-01", "statement": "A flat fee is charged once."},
        {"id": "RULE-09", "statement": "GST is added to the fee."},
    ]
    rate = records[2]
    assert [rule["id"] for rule in rate["relevant_rules"]] == ["RULE-03"]
    assert records[1]["diff"]["legacy"]["entry_type"] == "LATE_FEE"


def test_rate_reset_against_interest_shows_rate_accrual_and_calendar_rules():
    diff = {
        "path": "ledger.first_divergence",
        "index": 25,
        "legacy": {"entry_type": "BILL_INTEREST", "amount": "7895.25"},
        "new": {"entry_type": "RATE_RESET", "amount": "8.75"},
    }
    rules = [
        {"id": "RULE-A", "kind": "accrual", "statement": "Interest accrues daily."},
        {"id": "RULE-C", "kind": "calendar", "statement": "Day count follows the product."},
        {"id": "RULE-F", "kind": "fee", "statement": "A fee is charged."},
        {"id": "RULE-R", "kind": "rate", "statement": "Floating rate resets from the benchmark."},
    ]
    assert [rule["id"] for rule in classify.relevant_rules(diff, rules)] == [
        "RULE-A",
        "RULE-R",
        "RULE-C",
    ]


def test_missing_case_still_builds_a_record():
    report = [
        {
            "case_id": "NOPE",
            "scenario": "missing",
            "status": "MISMATCH",
            "diffs": [{"path": "loan.status", "legacy": "ACTIVE", "new": "CLOSED"}],
        }
    ]
    records = classify.build_records(report, [], {}, {})
    assert records[0]["product"] == {
        "product_code": None,
        "day_count": None,
        "rate_type": None,
    }
    assert records[0]["legacy_context"] == []
    assert records[0]["relevant_rules"] == []


def test_main_writes_classified_file_and_summary(tmp_path, capsys):
    golden = json.loads((ROOT / "golden" / "golden_master.json").read_text(encoding="utf-8"))
    case = next(item for item in golden if item["case_id"] == "C01")
    report = [
        {"case_id": "C01", "scenario": "on_time_act365", "status": "MATCH", "diffs": []},
        {
            "case_id": "C01",
            "scenario": "on_time_act365",
            "status": "MISMATCH",
            "diffs": [
                {"path": "summary.fees_charged", "legacy": "250.00", "new": "0.00"},
                {
                    "path": "ledger.first_divergence",
                    "index": 2,
                    "legacy": {
                        "entry_date": "2024-02-12",
                        "entry_type": "LATE_FEE",
                        "amount": "250.00",
                        "note": "inst 1",
                    },
                    "new": {
                        "entry_date": "2024-02-12",
                        "entry_type": "LATE_FEE",
                        "amount": "0.00",
                        "note": "inst 1",
                    },
                },
            ],
        },
    ]
    rules = {
        "module": "loan_eod",
        "rules": [
            {"id": "RULE-01", "kind": "fee", "statement": "A fee is charged."},
            {"id": "RULE-02", "kind": "penal_interest", "statement": "Penal interest stops at NPA."},
        ],
        "contradictions": [],
        "open_questions": [],
        "citation_failures": [],
    }
    report_path = tmp_path / "parity_report.json"
    rules_path = tmp_path / "rules.json"
    out_path = tmp_path / "classified.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    rules_path.write_text(json.dumps(rules), encoding="utf-8")
    payload = _load("null_handling.json")

    code = classify.main(
        ["--report", str(report_path), "--rules", str(rules_path), "--out", str(out_path)],
        client=QueueClient([payload, payload]),
    )
    assert code == 0
    entries = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(entries) == 2
    fee = next(item for item in entries if item["record"]["diff"]["path"] == "summary.fees_charged")
    assert fee["record"]["product"] == {
        "product_code": "HL-STD",
        "day_count": "ACT365",
        "rate_type": "FIXED",
    }
    assert fee["record"]["legacy_context"] == case["legacy_output"]["ledger"][0:2]
    assert fee["record"]["relevant_rules"] == [{"id": "RULE-01", "statement": "A fee is charged."}]
    assert fee["grok"]["label"] == "null_handling"
    assert fee["needs_human"] is False
    printed = capsys.readouterr().out
    assert "null_handling: 2" in printed
    assert "needs_human: 0" in printed
    assert "real_defect: 0" in printed
    assert "unknown: 0" in printed


@pytest.mark.live
def test_live_call_when_key_is_set():
    if not os.environ.get("XAI_API_KEY"):
        pytest.skip("XAI_API_KEY is not set")
    from tools.grok_client import OpenAIGrokClient

    schema = _schema()
    user = (ROOT / "schemas" / "examples" / "example_mismatch_input.json").read_text(encoding="utf-8")
    answer = OpenAIGrokClient.from_env().complete(
        name="mismatch",
        schema=schema,
        system=classify.build_system_prompt(),
        user=user,
    )
    jsonschema.validate(answer, schema)
