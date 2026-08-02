import json
from datetime import datetime, timedelta, timezone

import pytest

from zepto_discovery import config
from zepto_discovery.grok_client import KeyRotator
from zepto_discovery.synthesis import (
    RESEARCH_QUESTIONS,
    build_stage3_messages,
    get_or_compute_synthesis,
    parse_stage3_response,
    save_answers,
    synthesize,
)

RUN_DATE = datetime(2026, 8, 2, tzinfo=timezone.utc)


def days_ago(n):
    return (RUN_DATE - timedelta(days=n)).isoformat()


def make_tagged(review_id, days_old, text="substantive review text", tags=None):
    return {
        "review_id": review_id,
        "date": days_ago(days_old),
        "rating": 3,
        "text": text,
        "category": None,
        "sentiment": "neutral",
        "theme_tags": tags or ["a"],
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


def make_answer(question, review_ids, theme="Delivery"):
    return {
        "question": question,
        "answer": "Some synthesized answer.",
        "supporting_reviews": [
            {"review_id": rid, "excerpt": f"excerpt for {rid}", "theme": theme} for rid in review_ids
        ],
    }


def full_answer_set(review_ids):
    # 3 supporting reviews per question, cycling through the given ids.
    return [make_answer(q, review_ids[:3]) for q in RESEARCH_QUESTIONS]


# --- build_stage3_messages -------------------------------------------------


def test_build_stage3_messages_includes_questions_and_theme_excerpts():
    tagged = [make_tagged("r1", 1, text="Milk delivery was late again")]
    themes = [make_theme("Delivery speed", ["r1"])]

    messages = build_stage3_messages(themes, tagged)
    content = messages[1]["content"]
    for question in RESEARCH_QUESTIONS:
        assert question in content
    assert "Milk delivery was late again" in content
    assert "Delivery speed" in content


# --- parse_stage3_response ---------------------------------------------------


def test_parse_stage3_response_happy_path():
    raw = json.dumps(full_answer_set(["r1", "r2", "r3"]))
    answers = parse_stage3_response(raw, valid_review_ids={"r1", "r2", "r3"})
    assert len(answers) == 8
    assert answers[0]["question"] == RESEARCH_QUESTIONS[0]


def test_parse_stage3_response_raises_on_wrong_answer_count():
    raw = json.dumps(full_answer_set(["r1", "r2", "r3"])[:7])  # only 7, not 8
    with pytest.raises(ValueError, match="Expected 8 answers"):
        parse_stage3_response(raw, valid_review_ids={"r1", "r2", "r3"})


def test_parse_stage3_response_raises_on_missing_field():
    answers = full_answer_set(["r1", "r2", "r3"])
    del answers[0]["answer"]
    with pytest.raises(ValueError, match="missing required fields"):
        parse_stage3_response(json.dumps(answers), valid_review_ids={"r1", "r2", "r3"})


def test_parse_stage3_response_raises_on_too_few_supporting_reviews():
    answers = full_answer_set(["r1", "r2", "r3"])
    answers[0]["supporting_reviews"] = answers[0]["supporting_reviews"][:2]  # only 2
    with pytest.raises(ValueError, match="expected 3-5"):
        parse_stage3_response(json.dumps(answers), valid_review_ids={"r1", "r2", "r3"})


def test_parse_stage3_response_raises_on_too_many_supporting_reviews():
    answers = full_answer_set(["r1", "r2", "r3"])
    answers[0]["supporting_reviews"] = answers[0]["supporting_reviews"] * 3  # 6
    with pytest.raises(ValueError, match="expected 3-5"):
        parse_stage3_response(json.dumps(answers), valid_review_ids={"r1", "r2", "r3"})


def test_parse_stage3_response_raises_on_malformed_support_entry():
    answers = full_answer_set(["r1", "r2", "r3"])
    del answers[0]["supporting_reviews"][0]["theme"]
    with pytest.raises(ValueError, match="Malformed supporting_review entry"):
        parse_stage3_response(json.dumps(answers), valid_review_ids={"r1", "r2", "r3"})


def test_parse_stage3_response_raises_on_unknown_review_id():
    answers = full_answer_set(["r1", "r2", "r999"])
    with pytest.raises(ValueError, match="unknown review_id"):
        parse_stage3_response(json.dumps(answers), valid_review_ids={"r1", "r2", "r3"})


# --- synthesize: thin-pool preflight guard ----------------------------------


def test_synthesize_raises_before_calling_llm_when_pool_too_thin():
    tagged = [make_tagged("r1", 1), make_tagged("r2", 1)]  # only 2, need >= 3
    themes_result = {"timeframe_days": 90, "pool_size": 2, "is_thin": True, "themes": [make_theme("X", ["r1", "r2"])]}
    rotator = KeyRotator(["k1"])
    calls = {"n": 0}

    def http_post(*a, **kw):
        calls["n"] += 1
        return llm_response("should never be reached")

    with pytest.raises(ValueError, match="not enough to satisfy"):
        synthesize(themes_result, tagged, rotator, run_date=RUN_DATE, http_post=http_post)
    assert calls["n"] == 0  # confirms the LLM was never called


def test_synthesize_succeeds_with_exactly_minimum_pool():
    tagged = [make_tagged("r1", 1), make_tagged("r2", 1), make_tagged("r3", 1)]
    themes_result = {"timeframe_days": 90, "pool_size": 3, "is_thin": True, "themes": [make_theme("X", ["r1", "r2", "r3"])]}
    rotator = KeyRotator(["k1"])
    http_post = lambda *a, **kw: llm_response(json.dumps(full_answer_set(["r1", "r2", "r3"])))

    result = synthesize(themes_result, tagged, rotator, run_date=RUN_DATE, http_post=http_post)
    assert len(result["answers"]) == 8
    assert result["is_thin"] is True


# --- synthesize: retry behavior --------------------------------------------


def test_synthesize_retries_once_on_malformed_then_succeeds():
    tagged = [make_tagged(f"r{i}", 1) for i in range(1, 4)]
    themes_result = {"timeframe_days": 90, "pool_size": 3, "is_thin": False, "themes": [make_theme("X", ["r1", "r2", "r3"])]}
    rotator = KeyRotator(["k1"])
    calls = []

    def http_post(url, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return llm_response("not json")
        return llm_response(__import__("json").dumps(full_answer_set(["r1", "r2", "r3"])))

    result = synthesize(themes_result, tagged, rotator, run_date=RUN_DATE, http_post=http_post)
    assert len(calls) == 2
    assert len(result["answers"]) == 8


def test_synthesize_raises_after_exhausting_retries():
    tagged = [make_tagged(f"r{i}", 1) for i in range(1, 4)]
    themes_result = {"timeframe_days": 90, "pool_size": 3, "is_thin": False, "themes": [make_theme("X", ["r1", "r2", "r3"])]}
    rotator = KeyRotator(["k1"])
    http_post = lambda *a, **kw: llm_response("still not json")

    with pytest.raises(ValueError, match="failed to produce valid JSON"):
        synthesize(themes_result, tagged, rotator, run_date=RUN_DATE, http_post=http_post, max_retries=1)


# --- get_or_compute_synthesis (lazy + cached) -------------------------------


def test_get_or_compute_synthesis_computes_once_then_reuses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SYNTHESIS_DIR", tmp_path)
    tagged = [make_tagged(f"r{i}", 1) for i in range(1, 4)]
    themes_result = {"timeframe_days": 90, "pool_size": 3, "is_thin": False, "themes": [make_theme("X", ["r1", "r2", "r3"])]}
    rotator = KeyRotator(["k1"])
    call_count = {"n": 0}

    def http_post(url, headers, json, timeout):
        call_count["n"] += 1
        return llm_response(__import__("json").dumps(full_answer_set(["r1", "r2", "r3"])))

    result1, cached1 = get_or_compute_synthesis(themes_result, tagged, rotator, run_date=RUN_DATE, http_post=http_post)
    result2, cached2 = get_or_compute_synthesis(themes_result, tagged, rotator, run_date=RUN_DATE, http_post=http_post)

    assert cached1 is False
    assert cached2 is True
    assert call_count["n"] == 1
    assert result1 == result2


def test_get_or_compute_synthesis_force_recomputes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SYNTHESIS_DIR", tmp_path)
    tagged = [make_tagged(f"r{i}", 1) for i in range(1, 4)]
    themes_result = {"timeframe_days": 90, "pool_size": 3, "is_thin": False, "themes": [make_theme("X", ["r1", "r2", "r3"])]}
    rotator = KeyRotator(["k1"])
    call_count = {"n": 0}

    def http_post(url, headers, json, timeout):
        call_count["n"] += 1
        return llm_response(__import__("json").dumps(full_answer_set(["r1", "r2", "r3"])))

    get_or_compute_synthesis(themes_result, tagged, rotator, run_date=RUN_DATE, http_post=http_post)
    _, cached = get_or_compute_synthesis(themes_result, tagged, rotator, run_date=RUN_DATE, http_post=http_post, force=True)

    assert cached is False
    assert call_count["n"] == 2


# --- save_answers ------------------------------------------------------------


def test_save_answers_writes_expected_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SYNTHESIS_DIR", tmp_path)
    result = {"timeframe_days": 30, "pool_size": 3, "is_thin": False, "answers": full_answer_set(["r1", "r2", "r3"])}
    path = save_answers(result, run_date=RUN_DATE)
    assert path.name == "answers_2026-08-02_30d.json"
