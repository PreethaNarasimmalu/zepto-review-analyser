import pytest

from zepto_discovery.grok_client import (
    AllKeysExhaustedError,
    GrokAPIError,
    KeyRotator,
    call_with_failover,
    chat_completion,
)


class FakeClock:
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


def ok_response(content="hello"):
    return FakeResponse(200, {"choices": [{"message": {"content": content}}]})


# --- KeyRotator -----------------------------------------------------------


def test_round_robins_across_keys():
    clock = FakeClock()
    rotator = KeyRotator(["k1", "k2", "k3"], clock=clock)
    first_lap = list(rotator.iter_available_keys())
    assert first_lap == ["k1", "k2", "k3"]


def test_cooled_down_key_is_skipped_until_cooldown_expires():
    clock = FakeClock()
    rotator = KeyRotator(["k1", "k2"], cooldown_seconds=60, clock=clock)
    rotator.cool_down("k1")

    assert list(rotator.iter_available_keys()) == ["k2"]

    clock.advance(61)
    assert set(rotator.iter_available_keys()) == {"k1", "k2"}


def test_available_count_reflects_cooldowns():
    clock = FakeClock()
    rotator = KeyRotator(["k1", "k2", "k3"], clock=clock)
    assert rotator.available_count() == 3
    rotator.cool_down("k1")
    assert rotator.available_count() == 2


# --- call_with_failover -----------------------------------------------------


def test_call_with_failover_returns_first_success():
    rotator = KeyRotator(["k1", "k2"])
    result = call_with_failover(rotator, lambda key: f"ok-{key}")
    assert result == "ok-k1"


def test_call_with_failover_falls_back_on_rate_limit():
    rotator = KeyRotator(["k1", "k2"])

    def request_fn(key):
        if key == "k1":
            raise GrokAPIError(429, "rate limited")
        return f"ok-{key}"

    result = call_with_failover(rotator, request_fn)
    assert result == "ok-k2"
    # k1 should now be cooling down
    assert rotator.available_count() == 1


def test_call_with_failover_raises_when_all_keys_fail():
    rotator = KeyRotator(["k1", "k2"])

    def request_fn(key):
        raise GrokAPIError(500, "server error")

    with pytest.raises(AllKeysExhaustedError):
        call_with_failover(rotator, request_fn)


def test_call_with_failover_raises_when_no_keys_available():
    clock = FakeClock()
    rotator = KeyRotator(["k1"], clock=clock)
    rotator.cool_down("k1")

    with pytest.raises(AllKeysExhaustedError):
        call_with_failover(rotator, lambda key: "unreachable")


def test_key_recovers_after_cooldown_and_can_succeed():
    clock = FakeClock()
    rotator = KeyRotator(["k1"], cooldown_seconds=30, clock=clock)
    rotator.cool_down("k1")
    with pytest.raises(AllKeysExhaustedError):
        call_with_failover(rotator, lambda key: "should not run")

    clock.advance(31)
    assert call_with_failover(rotator, lambda key: f"ok-{key}") == "ok-k1"


# --- chat_completion (HTTP layer mocked) -----------------------------------


def test_chat_completion_returns_message_content():
    rotator = KeyRotator(["k1"])
    http_post = lambda *a, **kw: ok_response("the answer")
    result = chat_completion(rotator, messages=[{"role": "user", "content": "hi"}], http_post=http_post)
    assert result == "the answer"


def test_chat_completion_fails_over_on_429_then_succeeds():
    rotator = KeyRotator(["k1", "k2"])
    calls = []

    def http_post(url, headers, json, timeout):
        key = headers["Authorization"].split(" ")[1]
        calls.append(key)
        if key == "k1":
            return FakeResponse(429, text="rate limited")
        return ok_response("recovered")

    result = chat_completion(rotator, messages=[{"role": "user", "content": "hi"}], http_post=http_post)
    assert result == "recovered"
    assert calls == ["k1", "k2"]


def test_chat_completion_raises_clear_error_when_all_keys_down():
    rotator = KeyRotator(["k1", "k2"])
    http_post = lambda *a, **kw: FakeResponse(500, text="boom")

    with pytest.raises(AllKeysExhaustedError):
        chat_completion(rotator, messages=[{"role": "user", "content": "hi"}], http_post=http_post)
