"""Grok API client with key rotation and automatic failover.

Reads all keys from Streamlit secrets (never hardcoded or .env). Rotates
across keys round-robin; on a rate-limit or error response, cools that key
down and retries the same request against the next available one. Used by
Stage 1/2/3 tagging/clustering/synthesis and by Phase 10's re-query.
"""

import time

import requests

GROK_API_URL = "https://api.x.ai/v1/chat/completions"
DEFAULT_MODEL = "grok-4-fast"
DEFAULT_COOLDOWN_SECONDS = 60


class GrokAPIError(RuntimeError):
    """Raised for any non-2xx response from the Grok API."""

    def __init__(self, status_code, message):
        super().__init__(f"Grok API error {status_code}: {message}")
        self.status_code = status_code


class AllKeysExhaustedError(RuntimeError):
    """Raised when every key is either rate-limited/cooling down or failed
    for this request."""


def load_api_keys():
    """Reads the key list from Streamlit secrets: GROK_API_KEYS = [...]."""
    import streamlit as st

    try:
        keys = list(st.secrets["GROK_API_KEYS"])
    except Exception as e:
        raise RuntimeError(
            "GROK_API_KEYS not found in Streamlit secrets. Add a "
            "GROK_API_KEYS = [\"key1\", \"key2\", ...] array to "
            ".streamlit/secrets.toml (local) or the deployed app's "
            "Secrets settings."
        ) from e
    if not keys:
        raise RuntimeError("GROK_API_KEYS is present but empty.")
    return keys


class KeyRotator:
    """Round-robins across a fixed set of keys, skipping ones on cooldown."""

    def __init__(self, keys, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS, clock=time.monotonic):
        keys = list(keys)
        if not keys:
            raise ValueError("KeyRotator requires at least one key.")
        self._keys = keys
        self._cooldown_until = {k: 0.0 for k in keys}
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._next_index = 0

    def cool_down(self, key):
        self._cooldown_until[key] = self._clock() + self._cooldown_seconds

    def available_count(self):
        now = self._clock()
        return sum(1 for k in self._keys if self._cooldown_until[k] <= now)

    def iter_available_keys(self):
        """Yields each currently-available key at most once, in round-robin
        order starting after the last key used — one full lap, not an
        infinite cycle, so a caller always terminates."""
        now = self._clock()
        n = len(self._keys)
        start = self._next_index
        for offset in range(n):
            idx = (start + offset) % n
            key = self._keys[idx]
            if self._cooldown_until[key] <= now:
                self._next_index = (idx + 1) % n
                yield key


def call_with_failover(rotator, request_fn):
    """Tries `request_fn(key)` across the rotator's available keys in turn.

    Returns the first successful result. A GrokAPIError from request_fn
    cools that key down and moves on to the next one. Raises
    AllKeysExhaustedError if no key is available or every available key
    failed for this request.
    """
    last_exception = None
    tried_any = False
    for key in rotator.iter_available_keys():
        tried_any = True
        try:
            return request_fn(key)
        except GrokAPIError as e:
            last_exception = e
            rotator.cool_down(key)
            continue
    if not tried_any:
        raise AllKeysExhaustedError(
            "No Grok API keys are currently available — all are cooling down."
        ) from last_exception
    raise AllKeysExhaustedError(
        "All available Grok API keys failed or were rate-limited for this request."
    ) from last_exception


def _post_chat_completion(key, messages, model, temperature, timeout, http_post):
    response = http_post(
        GROK_API_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "temperature": temperature},
        timeout=timeout,
    )
    if response.status_code != 200:
        raise GrokAPIError(response.status_code, response.text[:500])
    return response.json()["choices"][0]["message"]["content"]


def chat_completion(rotator, messages, model=None, temperature=0, timeout=60, http_post=None):
    """Runs one chat completion with key rotation/failover.

    `http_post` is an injectable seam for testing (defaults to
    requests.post); it must return an object with `.status_code`, `.json()`,
    and `.text`, matching requests.Response's interface.
    """
    model = model or DEFAULT_MODEL
    http_post = http_post or requests.post
    return call_with_failover(
        rotator,
        lambda key: _post_chat_completion(key, messages, model, temperature, timeout, http_post),
    )
