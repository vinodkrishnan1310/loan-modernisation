"""Grok calls for this repo.

The API key is read from the environment (`XAI_API_KEY`) when a live client is
built. Nothing in this module embeds a key. Tests hand a recorded response
through `RecordedResponseClient` and do not open a network connection.

Live calls use structured output, temperature 0, and a configurable timeout.
A call is tried once and then retried twice with backoff. If it still fails,
or the parsed JSON does not match the schema, the client raises
`GrokUnavailable`.

Set GROK_VERBOSE=0 to silence progress on stderr. Set GROK_TIMEOUT_SECONDS to
override the per-attempt timeout (default 120).
"""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone

import httpx2
import jsonschema

XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.7"
DEFAULT_TIMEOUT_SECONDS = 600
# One try, then two retries. Delays are the pauses before each retry.
RETRY_DELAYS_SECONDS = (1, 2)
DEFAULT_HEARTBEAT_SECONDS = 15


def timeout_seconds() -> float:
    raw = os.environ.get("GROK_TIMEOUT_SECONDS", "").strip()
    if raw:
        return float(raw)
    return float(DEFAULT_TIMEOUT_SECONDS)


def _verbose() -> bool:
    return os.environ.get("GROK_VERBOSE", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _log(message: str) -> None:
    if not _verbose():
        return
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[grok {ts}Z] {message}", file=sys.stderr, flush=True)


def heartbeat_seconds() -> float:
    raw = os.environ.get("GROK_HEARTBEAT_SECONDS", "").strip()
    if raw:
        return max(1.0, float(raw))
    return float(DEFAULT_HEARTBEAT_SECONDS)


def httpx_timeout() -> httpx2.Timeout:
    """Read timeout for Grok JSON generation; SDK default is 600s if this is not set."""
    read = timeout_seconds()
    return httpx2.Timeout(read, connect=30.0, write=30.0, pool=30.0)


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

        sdk = OpenAI(
            api_key=key,
            base_url=XAI_BASE_URL,
            timeout=httpx_timeout(),
            max_retries=0,
        )
        return cls(create=sdk.chat.completions.create, model=model_name())

    def complete(self, *, name: str, schema: dict, system: str, user: str) -> dict:
        last: Exception | None = None
        attempts = len(RETRY_DELAYS_SECONDS) + 1
        timeout = timeout_seconds()
        _log(
            f"schema={name!r} model={self.model!r} "
            f"timeout={timeout:g}s max_attempts={attempts}"
        )
        for attempt in range(attempts):
            n = attempt + 1
            _log(f"attempt {n}/{attempts} started")
            t0 = time.monotonic()
            try:
                result = self._once(
                    name=name, schema=schema, system=system, user=user
                )
                elapsed = time.monotonic() - t0
                _log(f"attempt {n}/{attempts} ok ({elapsed:.1f}s)")
                return result
            except Exception as exc:  # noqa: BLE001 — any failed call is retried
                last = exc
                elapsed = time.monotonic() - t0
                if attempt < len(RETRY_DELAYS_SECONDS):
                    delay = RETRY_DELAYS_SECONDS[attempt]
                    _log(
                        f"attempt {n}/{attempts} failed after {elapsed:.1f}s: "
                        f"{type(exc).__name__}: {exc}; retry in {delay}s"
                    )
                    self._sleep(delay)
                else:
                    _log(
                        f"attempt {n}/{attempts} failed after {elapsed:.1f}s: "
                        f"{type(exc).__name__}: {exc}; no retries left"
                    )
        raise GrokUnavailable(_short(last)) from last

    def _once(self, *, name: str, schema: dict, system: str, user: str) -> dict:
        limit = timeout_seconds()
        stop = threading.Event()
        t0 = time.monotonic()

        def heartbeat() -> None:
            while not stop.wait(heartbeat_seconds()):
                elapsed = time.monotonic() - t0
                _log(
                    f"still waiting... {elapsed:.0f}s elapsed "
                    f"(read timeout {limit:g}s; retries only after HTTP returns or times out)"
                )

        pulse = threading.Thread(target=heartbeat, name="grok-heartbeat", daemon=True)
        pulse.start()
        try:
            response = self._create(
                model=self.model,
                temperature=0,
                timeout=httpx_timeout(),
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
        finally:
            stop.set()
            pulse.join(timeout=0.1)

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
