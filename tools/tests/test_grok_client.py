"""Grok client contract tests. These tests do not use the network."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.grok_client import (
    DEFAULT_MODEL,
    XAI_BASE_URL,
    GrokUnavailable,
    OpenAIGrokClient,
    httpx_timeout,
    model_name,
)

ROOT = Path(__file__).resolve().parents[2]


def _response(payload):
    message = SimpleNamespace(content=json.dumps(payload))
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _schema():
    return json.loads((ROOT / "schemas" / "mismatch.schema.json").read_text(encoding="utf-8"))


def _valid():
    return json.loads(
        (ROOT / "schemas" / "examples" / "example_mismatch_output.json").read_text(encoding="utf-8")
    )


def test_model_name_defaults_and_follows_env(monkeypatch):
    monkeypatch.delenv("GROK_MODEL", raising=False)
    assert model_name() == DEFAULT_MODEL
    assert DEFAULT_MODEL == "grok-4.3"
    monkeypatch.setenv("GROK_MODEL", "grok-test")
    assert model_name() == "grok-test"


def test_from_env_requires_key_and_targets_xai(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(GrokUnavailable, match="XAI_API_KEY"):
        OpenAIGrokClient.from_env()

    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        class chat:
            class completions:
                create = staticmethod(lambda **kwargs: None)

    monkeypatch.setenv("XAI_API_KEY", "present")
    monkeypatch.delenv("GROK_MODEL", raising=False)
    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    client = OpenAIGrokClient.from_env()
    assert captured["base_url"] == XAI_BASE_URL
    assert captured["timeout"] == httpx_timeout()
    assert captured["api_key"] == os.environ["XAI_API_KEY"]
    assert client.model == "grok-4.3"


def test_complete_sends_structured_output_and_validates():
    schema = _schema()
    payload = _valid()
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return _response(payload)

    client = OpenAIGrokClient(create=create, model="grok-4.3", sleep=lambda _seconds: None)
    result = client.complete(name="mismatch", schema=schema, system="system", user="user")
    assert result == payload
    assert seen["model"] == "grok-4.3"
    assert seen["temperature"] == 0
    assert seen["timeout"] == httpx_timeout()
    assert seen["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]
    assert seen["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "mismatch", "schema": schema, "strict": True},
    }
    assert seen["response_format"]["json_schema"]["schema"] is schema


def test_two_retries_then_grok_unavailable():
    calls = {"n": 0}
    sleeps = []

    def create(**kwargs):
        calls["n"] += 1
        raise TimeoutError("timed out")

    client = OpenAIGrokClient(create=create, model="grok-4.3", sleep=sleeps.append)
    with pytest.raises(GrokUnavailable, match="timed out"):
        client.complete(name="mismatch", schema=_schema(), system="s", user="u")
    assert calls["n"] == 3
    assert sleeps == [1, 2]


def test_schema_mismatch_is_a_failed_call():
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        return _response({"label": "not-a-label"})

    client = OpenAIGrokClient(create=create, model="grok-4.3", sleep=lambda _seconds: None)
    with pytest.raises(GrokUnavailable, match="schema"):
        client.complete(name="mismatch", schema=_schema(), system="s", user="u")
    assert calls["n"] == 3
