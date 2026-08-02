import json
from datetime import datetime, timedelta, timezone

import pytest

from zepto_discovery.grok_client import KeyRotator
from zepto_discovery.requery import answer_question, build_requery_messages, parse_requery_response

RUN_DATE = datetime(2026, 8, 2, tzinfo=timezone.utc)


def days_ago(n):
    return (RUN_DATE - timedelta(days=n)).isoformat()


def make_tagged(review_id, days_old, text="substantive review text"):
    return {
        "review_id": review_id,
        "date": days_ago(days_old),
        "rating": 3,
        "text": text,
        "category": None,
        "sentiment": "neutral",
        "theme_tags": ["a"],
    }


def make_theme(name, example_ids, count=10):
    return {"name": name, "description": f"{name} description", "count": count, "example_review_ids": example_ids}


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


def llm_response(content):
    return FakeResponse(200, {"choices": [{"message": {"content": content}}]})


def good_answer(review_ids, theme="Notifications"):
    return {
        "answer": "Some synthesized answer to the ad-hoc question.",
        "supporting_reviews": [
            {"review_id": rid, "excerpt": f"excerpt for {rid}", "theme": theme} for rid in review_ids
        ],
    }


QUESTION = "Do users mention discovery via banners or push notifications?"


# --- build_requery_messages -------------------------------------------------


def test_build_requery_messages_includes_question_and_theme_data():
    tagged = [make_tagged("r1", 1, text="Too many push notifications about sales")]
    themes = [make_theme("Notifications", ["r1"])]

    messages = build_requery_messages(QUESTION, themes, tagged)
    content = messages[1]["content"]
    assert QUESTION in content
    assert "Too many push notifications about sales" in content
    assert "Notifications" in content


# --- parse_requery_response --------------------------------------------------


def test_parse_requery_response_happy_path():
    raw = json.dumps(good_answer(["r1", "r2", "r3"]))
    result = parse_requery_response(raw, valid_review_ids={"r1", "r2", "r3"})
    assert result["answer"]
    assert len(result["supporting_reviews"]) == 3


def test_parse_requery_response_raises_on_missing_field():
    answer = good_answer(["r1", "r2", "r3"])
    del answer["answer"]
    with pytest.raises(ValueError, match="missing required fields"):
        parse_requery_response(json.dumps(answer), valid_review_ids={"r1", "r2", "r3"})


def test_parse_requery_response_raises_on_too_few_supporting_reviews():
    answer = good_answer(["r1", "r2", "r3"])
    answer["supporting_reviews"] = answer["supporting_reviews"][:2]
    with pytest.raises(ValueError, match="expected 3-5"):
        parse_requery_response(json.dumps(answer), valid_review_ids={"r1", "r2", "r3"})


def test_parse_requery_response_raises_on_unknown_review_id():
    answer = good_answer(["r1", "r2", "r999"])
    with pytest.raises(ValueError, match="unknown review_id"):
        parse_requery_response(json.dumps(answer), valid_review_ids={"r1", "r2", "r3"})


def test_parse_requery_response_raises_on_malformed_support_entry():
    answer = good_answer(["r1", "r2", "r3"])
    del answer["supporting_reviews"][0]["theme"]
    with pytest.raises(ValueError, match="Malformed supporting_review entry"):
        parse_requery_response(json.dumps(answer), valid_review_ids={"r1", "r2", "r3"})


# --- answer_question: thin-pool preflight guard -----------------------------


def test_answer_question_raises_before_calling_llm_when_pool_too_thin():
    tagged = [make_tagged("r1", 1), make_tagged("r2", 1)]  # only 2, need >= 3
    themes_result = {"timeframe_days": 90, "themes": [make_theme("X", ["r1", "r2"])]}
    rotator = KeyRotator(["k1"])
    calls = {"n": 0}

    def http_post(*a, **kw):
        calls["n"] += 1
        return llm_response("should never be reached")

    with pytest.raises(ValueError, match="not enough to satisfy"):
        answer_question(QUESTION, themes_result, tagged, rotator, run_date=RUN_DATE, http_post=http_post)
    assert calls["n"] == 0


# --- answer_question: happy path, exactly one call -------------------------


def test_answer_question_makes_exactly_one_llm_call_on_success():
    tagged = [make_tagged(f"r{i}", 1) for i in range(1, 4)]
    themes_result = {"timeframe_days": 90, "themes": [make_theme("X", ["r1", "r2", "r3"])]}
    rotator = KeyRotator(["k1"])
    calls = {"n": 0}

    def http_post(url, headers, json, timeout):
        calls["n"] += 1
        return llm_response(__import__("json").dumps(good_answer(["r1", "r2", "r3"])))

    result = answer_question(QUESTION, themes_result, tagged, rotator, run_date=RUN_DATE, http_post=http_post)

    assert calls["n"] == 1
    assert result["question"] == QUESTION
    assert len(result["supporting_reviews"]) == 3
    for support in result["supporting_reviews"]:
        assert support["review_id"] in {"r1", "r2", "r3"}


# --- answer_question: retry behavior ---------------------------------------


def test_answer_question_retries_once_on_malformed_then_succeeds():
    tagged = [make_tagged(f"r{i}", 1) for i in range(1, 4)]
    themes_result = {"timeframe_days": 90, "themes": [make_theme("X", ["r1", "r2", "r3"])]}
    rotator = KeyRotator(["k1"])
    calls = []

    def http_post(url, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return llm_response("not json")
        return llm_response(__import__("json").dumps(good_answer(["r1", "r2", "r3"])))

    result = answer_question(QUESTION, themes_result, tagged, rotator, run_date=RUN_DATE, http_post=http_post)
    assert len(calls) == 2
    assert result["answer"]


def test_answer_question_raises_after_exhausting_retries():
    tagged = [make_tagged(f"r{i}", 1) for i in range(1, 4)]
    themes_result = {"timeframe_days": 90, "themes": [make_theme("X", ["r1", "r2", "r3"])]}
    rotator = KeyRotator(["k1"])
    http_post = lambda *a, **kw: llm_response("still not json")

    with pytest.raises(ValueError, match="failed to produce valid JSON"):
        answer_question(
            QUESTION, themes_result, tagged, rotator, run_date=RUN_DATE, http_post=http_post, max_retries=1
        )


def test_answer_question_only_cites_reviews_in_the_themes_timeframe():
    # Three in-window reviews (enough to clear the thin-pool preflight
    # guard on their own) plus one out-of-window review the model
    # shouldn't be able to cite.
    in_window = [make_tagged(f"r{i}", 1) for i in range(1, 4)]
    out_of_window = make_tagged("r99", 100)
    themes_result = {"timeframe_days": 7, "themes": [make_theme("X", ["r1", "r2", "r3"])]}
    rotator = KeyRotator(["k1"])
    # Model tries to cite the out-of-window review — should be rejected.
    http_post = lambda *a, **kw: llm_response(json.dumps(good_answer(["r1", "r2", "r99"])))

    with pytest.raises(ValueError, match="unknown review_id"):
        answer_question(
            QUESTION, themes_result, in_window + [out_of_window], rotator, run_date=RUN_DATE, http_post=http_post
        )
