"""
Contract test: the HTTP contract must not change when the engine changes.

api/contract/openapi.json is the contract. If this test fails after a change,
the change altered the public API. Fix the code, not the file, unless a person
has approved a contract change.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from main import app  # noqa: E402

CONTRACT = pathlib.Path(__file__).resolve().parents[1] / "contract" / "openapi.json"


def test_openapi_matches_contract():
    expected = json.loads(CONTRACT.read_text())
    actual = json.loads(json.dumps(app.openapi()))
    assert actual == expected
