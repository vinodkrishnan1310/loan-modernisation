"""Rule extractor tests. Recorded responses stand in for Grok. No network."""

import json
from pathlib import Path

import jsonschema
import pytest

from tools.grok_client import GrokUnavailable, RecordedResponseClient
from tools.rule_extractor import extract

ROOT = Path(__file__).resolve().parents[3]
RECORDED = Path(__file__).resolve().parent / "recorded"
FIXTURE = "SELECT fee;\n-- kept\n-- the fee is waived\n"


def _load(name):
    return json.loads((RECORDED / name).read_text(encoding="utf-8"))


def test_number_lines_uses_number_pipe_prefix():
    lines = [f"L{i}" for i in range(1, 43)]
    raw, numbered = extract.number_lines("\n".join(lines) + "\n")
    assert raw[41] == "L42"
    assert numbered.splitlines()[41] == "  42| L42"
    assert numbered.splitlines()[0] == "   1| L1"


def test_recorded_response_matches_schema():
    schema = json.loads((ROOT / "schemas" / "rules.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(_load("rules_ok.json"), schema)
    jsonschema.validate(_load("rules_bad_citation.json"), schema)


def test_citation_checker_flags_one_bad_citation():
    lines = ["SELECT fee;", "", "-- the fee is waived"]
    document = {
        "rules": [
            {
                "id": "RULE-01",
                "statement": "Selects a fee.",
                "source": {"file": "proc.sql", "start": 2, "end": 2},
            }
        ],
        "contradictions": [
            {
                "comment_source": {"file": "proc.sql", "start": 3, "end": 3},
                "code_source": {"file": "proc.sql", "start": 1, "end": 1},
                "description": "Comment and code disagree.",
            }
        ],
    }
    failures = extract.check_citations(document, lines, "proc.sql")
    assert failures == [
        {
            "location": "rules[0].source",
            "file": "proc.sql",
            "start": 2,
            "end": 2,
            "reason": extract.REASON_BLANK,
        }
    ]
    assert document["rules"][0]["statement"] == "Selects a fee."
    assert "citation_failures" not in document


def test_citation_checker_reports_every_failure():
    lines = ["SELECT fee;", "-- kept", "-- the fee is waived"]
    document = {
        "rules": [
            {"source": {"file": "other.sql", "start": 1, "end": 1}},
            {"source": {"file": "proc.sql", "start": 99, "end": 100}},
        ],
        "contradictions": [
            {
                "comment_source": {"file": "proc.sql", "start": 3, "end": 1},
                "code_source": {"file": "proc.sql", "start": 1, "end": 1},
            }
        ],
    }
    failures = extract.check_citations(document, lines, "proc.sql")
    assert [item["reason"] for item in failures] == [
        extract.REASON_FILE,
        extract.REASON_OUTSIDE,
        extract.REASON_OUTSIDE,
    ]
    assert [item["location"] for item in failures] == [
        "rules[0].source",
        "rules[1].source",
        "contradictions[0].comment_source",
    ]


def test_extract_sends_schema_prompt_and_numbered_file(tmp_path):
    recorded = _load("rules_ok.json")
    captured = {}

    class Capture:
        def complete(self, *, name, schema, system, user):
            captured["name"] = name
            captured["schema"] = schema
            captured["system"] = system
            captured["user"] = user
            return json.loads(json.dumps(recorded))

    source = tmp_path / "proc.sql"
    source.write_text(FIXTURE, encoding="utf-8")
    out = tmp_path / "rules.json"
    code = extract.main(["--file", str(source), "--out", str(out)], client=Capture())

    assert code == 0
    schema = json.loads((ROOT / "schemas" / "rules.schema.json").read_text(encoding="utf-8"))
    assert captured["name"] == "rules"
    assert captured["schema"] == schema
    assert captured["system"] == (ROOT / "prompts" / "extractor_system.md").read_text(encoding="utf-8")
    assert "File name: proc.sql" in captured["user"]
    assert "   1| SELECT fee;" in captured["user"]
    assert "   3| -- the fee is waived" in captured["user"]


def test_exit_codes(tmp_path, capsys):
    source = tmp_path / "proc.sql"
    source.write_text(FIXTURE, encoding="utf-8")
    ok_out = tmp_path / "ok.json"
    bad_out = tmp_path / "bad.json"
    recorded_ok = _load("rules_ok.json")
    recorded_bad = _load("rules_bad_citation.json")

    ok_code = extract.main(
        ["--file", str(source), "--out", str(ok_out)],
        client=RecordedResponseClient(recorded_ok),
    )
    bad_code = extract.main(
        ["--file", str(source), "--out", str(bad_out)],
        client=RecordedResponseClient(recorded_bad),
    )

    assert ok_code == 0
    assert bad_code == 2
    written_ok = json.loads(ok_out.read_text(encoding="utf-8"))
    written_bad = json.loads(bad_out.read_text(encoding="utf-8"))
    assert written_ok["citation_failures"] == []
    assert written_ok["rules"] == recorded_ok["rules"]
    assert written_ok["contradictions"] == recorded_ok["contradictions"]
    assert written_ok["open_questions"] == recorded_ok["open_questions"]
    assert written_bad["rules"] == recorded_bad["rules"]
    assert written_bad["rules"][0]["statement"] == "The procedure selects a fee amount."
    assert written_bad["citation_failures"] == [
        {
            "location": "rules[0].source",
            "file": "proc.sql",
            "start": 40,
            "end": 41,
            "reason": extract.REASON_OUTSIDE,
        }
    ]
    printed = capsys.readouterr().out
    assert "1 rules, 1 contradictions, 1 open questions, 0 citation failures" in printed
    assert "1 rules, 1 contradictions, 1 open questions, 1 citation failures" in printed


def test_grok_unavailable_exits_1_without_writing(tmp_path, capsys):
    source = tmp_path / "proc.sql"
    source.write_text(FIXTURE, encoding="utf-8")
    out = tmp_path / "missing.json"

    class Boom:
        def complete(self, *, name, schema, system, user):
            raise GrokUnavailable("timeout")

    code = extract.main(["--file", str(source), "--out", str(out)], client=Boom())
    assert code == 1
    assert not out.exists()
    assert "Grok unavailable: timeout" in capsys.readouterr().err


def test_missing_file_exits_1(tmp_path):
    code = extract.main(
        ["--file", str(tmp_path / "no.sql"), "--out", str(tmp_path / "out.json")],
        client=RecordedResponseClient(_load("rules_ok.json")),
    )
    assert code == 1
