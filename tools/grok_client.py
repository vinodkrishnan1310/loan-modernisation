"""Grok calls for this repo.

The API key is read from the environment (`XAI_API_KEY`) when a live client is
built. Nothing in this module embeds a key. Tests hand a recorded response
through `RecordedResponseClient` and do not open a network connection.

Live calls use structured output, temperature 0, and a 30 second timeout.
A call is tried once and then retried twice with backoff. If it still fails,
or the parsed JSON does not match the schema, the client raises
`GrokUnavailable`.
"""

from __future__ import annotations

import copy
import json
import os
import time

import jsonschema

XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.7"
TIMEOUT_SECONDS = 30
# One try, then two retries. Delays are the pauses before each retry.
RETRY_DELAYS_SECONDS = (1, 2)


class GrokUnavailable(Exception):
    """The Grok call failed. The caller sends the item to a human."""


def model_name() -> str:
    return os.environ.get("GROK_MODEL", DEFAULT_MODEL)


def _short(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if len(text) <= 500:
        return text
    return text[:497] + "..."


def validate_payload(payload: dict, schema: dict) -> dict:
    """Validate a parsed response. A schema failure is a failed call."""
    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError as exc:
        raise GrokUnavailable(
            f"response did not match the schema: {exc.message}"
        ) from exc
    return payload


class RecordedResponseClient:
    """Hands a recorded response to the caller. No network."""

    def __init__(self, response: dict):
        self._response = response

    def complete(self, *, name: str, schema: dict, system: str, user: str) -> dict:
        validate_payload(self._response, schema)
        return copy.deepcopy(self._response)


class OpenAIGrokClient:
    """Live client. `create` is the OpenAI chat completions create callable."""

    def __init__(self, *, create, model: str, sleep=time.sleep):
        self._create = create
        self.model = model
        self._sleep = sleep

    @classmethod
    def from_env(cls) -> "OpenAIGrokClient":
        key = os.environ.get("XAI_API_KEY")
        if not key:
            raise GrokUnavailable("XAI_API_KEY is not set")
        from openai import OpenAI

        sdk = OpenAI(api_key=key, base_url=XAI_BASE_URL, timeout=TIMEOUT_SECONDS)
        return cls(create=sdk.chat.completions.create, model=model_name())

    def complete(self, *, name: str, schema: dict, system: str, user: str) -> dict:
        last: Exception | None = None
        attempts = len(RETRY_DELAYS_SECONDS) + 1
        for attempt in range(attempts):
            try:
                return self._once(name=name, schema=schema, system=system, user=user)
            except Exception as exc:  # noqa: BLE001 — any failed call is retried
                last = exc
                if attempt < len(RETRY_DELAYS_SECONDS):
                    self._sleep(RETRY_DELAYS_SECONDS[attempt])
        raise GrokUnavailable(_short(last)) from last

    def _once(self, *, name: str, schema: dict, system: str, user: str) -> dict:
        response = self._create(
            model=self.model,
            temperature=0,
            timeout=TIMEOUT_SECONDS,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": name,
                    "schema": schema,
                    "strict": True,
                },
            },
        )
        content = _message_content(response)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise GrokUnavailable(f"response was not JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise GrokUnavailable("response JSON was not an object")
        return validate_payload(parsed, schema)


def _message_content(response) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise GrokUnavailable("response had no content") from exc
    if not isinstance(content, str) or not content.strip():
        raise GrokUnavailable("response had no content")
    return content
