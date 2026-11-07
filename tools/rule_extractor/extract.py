"""Recover business rules from a legacy stored procedure bundle.

Usage:
    python tools/rule_extractor/extract.py --file <sql> --out <json>

Every line is sent to Grok with its line number (`  42| text`). Grok's text is
written unchanged. This tool then checks each cited line range itself and
records failures under `citation_failures`. Exit 0 when every citation is
valid, 2 when any is not, 1 when the call fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.grok_client import GrokUnavailable, OpenAIGrokClient

SCHEMA_PATH = ROOT / "schemas" / "rules.schema.json"
PROMPT_PATH = ROOT / "prompts" / "extractor_system.md"

REASON_FILE = "file name does not match the file that was read"
REASON_OUTSIDE = "line range is outside the file"
REASON_BLANK = "all lines in the range are blank"
REASON_MISSING = "line range is missing"

SCHEMA_NAME = "rules"


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(1, f"{self.prog}: error: {message}\n")


def number_lines(text: str) -> tuple[list[str], str]:
    """Return the file's lines and the numbered text sent to Grok.

    The prefix is the line number, a pipe, and a space: `  42| text`.
    """
    lines = text.splitlines()
    width = max(4, len(str(len(lines))) if lines else 4)
    numbered = "\n".join(
        f"{number:>{width}}| {line}" for number, line in enumerate(lines, start=1)
    )
    return lines, numbered


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def user_message(file_name: str, numbered: str) -> str:
    return f"File name: {file_name}\n\n{numbered}"


def check_citations(document: dict, lines: list[str], file_name: str) -> list[dict]:
    """Check every line range Grok cited. Does not change Grok's text.

    A citation fails when the file name is not the file that was read, the
    range is not inside the file, or every line in the range is blank.
    """
    failures = []
    for index, rule in enumerate(document.get("rules") or []):
        source = rule.get("source") if isinstance(rule, dict) else None
        _append(failures, source, f"rules[{index}].source", lines, file_name)
    for index, item in enumerate(document.get("contradictions") or []):
        if not isinstance(item, dict):
            continue
        _append(
            failures,
            item.get("comment_source"),
            f"contradictions[{index}].comment_source",
            lines,
            file_name,
        )
        _append(
            failures,
            item.get("code_source"),
            f"contradictions[{index}].code_source",
            lines,
            file_name,
        )
    return failures


def _append(failures, source, location, lines, file_name):
    failure = _citation_failure(source, location, lines, file_name)
    if failure is not None:
        failures.append(failure)


def _citation_failure(source, location, lines, file_name):
    if not isinstance(source, dict):
        return {
            "location": location,
            "file": None,
            "start": None,
            "end": None,
            "reason": REASON_MISSING,
        }
    cited_file = source.get("file")
    start = source.get("start")
    end = source.get("end")
    if cited_file != file_name:
        reason = REASON_FILE
    elif _range_outside(start, end, len(lines)):
        reason = REASON_OUTSIDE
    elif all(not lines[i].strip() for i in range(start - 1, end)):
        reason = REASON_BLANK
    else:
        return None
    return {
        "location": location,
        "file": cited_file,
        "start": start,
        "end": end,
        "reason": reason,
    }


def _range_outside(start, end, count: int) -> bool:
    if not isinstance(start, int) or isinstance(start, bool):
        return True
    if not isinstance(end, int) or isinstance(end, bool):
        return True
    if start < 1 or end < 1 or start > count or end > count or start > end:
        return True
    return False


def extract_document(text: str, file_name: str, client) -> dict:
    """Call Grok and attach citation_failures. Grok's fields are not edited."""
    lines, numbered = number_lines(text)
    document = client.complete(
        name=SCHEMA_NAME,
        schema=load_schema(),
        system=load_system_prompt(),
        user=user_message(file_name, numbered),
    )
    document["citation_failures"] = check_citations(document, lines, file_name)
    return document


def print_summary(document: dict) -> None:
    rules = document.get("rules") or []
    contradictions = document.get("contradictions") or []
    questions = document.get("open_questions") or []
    failures = document.get("citation_failures") or []
    print(
        f"{len(rules)} rules, {len(contradictions)} contradictions, "
        f"{len(questions)} open questions, {len(failures)} citation failures"
    )


def main(argv: list[str] | None = None, client=None) -> int:
    parser = _Parser(description="Extract business rules from a legacy SQL bundle.")
    parser.add_argument("--file", required=True, help="SQL file to read")
    parser.add_argument("--out", required=True, help="Where to write the rules JSON")
    args = parser.parse_args(argv)

    source = Path(args.file)
    if not source.is_file():
        print(f"file not found: {source}", file=sys.stderr)
        return 1
    if client is None:
        try:
            client = OpenAIGrokClient.from_env()
        except GrokUnavailable as exc:
            print(f"Grok unavailable: {exc}", file=sys.stderr)
            return 1

    text = source.read_text(encoding="utf-8")
    try:
        document = extract_document(text, source.name, client)
    except GrokUnavailable as exc:
        print(f"Grok unavailable: {exc}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print_summary(document)
    failures = document.get("citation_failures") or []
    return 2 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
