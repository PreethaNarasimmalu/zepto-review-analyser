import json

import pytest

from zepto_discovery import config
from zepto_discovery.grok_client import KeyRotator
from zepto_discovery.tagging import (
    batch_reviews,
    build_stage1_messages,
    parse_stage1_response,
    save_tagged,
    tag_batch,
    tag_reviews,
)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


def llm_response(content):
    return FakeResponse(200, {"choices": [{"message": {"content": content}}]})


def make_review(review_id, text="Vegetables arrived rotten twice", rating=2, date="2026-07-01T00:00:00+00:00"):
    return {"review_id": review_id, "text": text, "rating": rating, "date": date, "thumbs_up": 0}


def tag_json(reviews_and_tags):
    """reviews_and_tags: list of (review_id, category, sentiment, theme_tags)."""
    return json.dumps(
        [
            {"review_id": rid, "category": cat, "sentiment": sent, "theme_tags": tags}
            for rid, cat, sent, tags in reviews_and_tags
        ]
    )


# --- batch_reviews -----------------------------------------------------


def test_batch_reviews_splits_evenly():
    reviews = [make_review(f"r{i}") for i in range(10)]
    batches = batch_reviews(reviews, batch_size=4)
    assert [len(b) for b in batches] == [4, 4, 2]


def test_batch_reviews_default_size_matches_config():
    reviews = [make_review(f"r{i}") for i in range(config.STAGE1_BATCH_SIZE + 5)]
    batches = batch_reviews(reviews)
    assert len(batches[0]) == config.STAGE1_BATCH_SIZE
    assert len(batches[-1]) == 5


# --- build_stage1_messages -----------------------------------------------


def test_build_stage1_messages_includes_review_ids_and_text():
    reviews = [make_review("r1", text="Milk delivery was late")]
    messages = build_stage1_messages(reviews)
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "r1" in messages[1]["content"]
    assert "Milk delivery was late" in messages[1]["content"]


# --- parse_stage1_response ------------------------------------------------


def test_parse_stage1_response_happy_path():
    raw = tag_json([("r1", "produce", "negative", ["rotten vegetables", "quality issue"])])
    result = parse_stage1_response(raw, expected_ids={"r1"})
    assert result["r1"]["category"] == "produce"
    assert result["r1"]["sentiment"] == "negative"
    assert result["r1"]["theme_tags"] == ["rotten vegetables", "quality issue"]


def test_parse_stage1_response_strips_code_fence():
    raw = "```json\n" + tag_json([("r1", None, "neutral", ["tag"])]) + "\n```"
    result = parse_stage1_response(raw, expected_ids={"r1"})
    assert result["r1"]["sentiment"] == "neutral"


def test_parse_stage1_response_raises_on_invalid_json():
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_stage1_response("not json at all", expected_ids={"r1"})


def test_parse_stage1_response_raises_on_missing_field():
    raw = json.dumps([{"review_id": "r1", "category": None, "sentiment": "positive"}])
    with pytest.raises(ValueError, match="missing required fields"):
        parse_stage1_response(raw, expected_ids={"r1"})


def test_parse_stage1_response_raises_on_bad_sentiment():
    raw = tag_json([("r1", None, "very happy", ["tag"])])
    with pytest.raises(ValueError, match="Unexpected sentiment"):
        parse_stage1_response(raw, expected_ids={"r1"})


def test_parse_stage1_response_raises_on_missing_review_id():
    raw = tag_json([("r1", None, "positive", ["tag"])])
    with pytest.raises(ValueError, match="missing"):
        parse_stage1_response(raw, expected_ids={"r1", "r2"})


# --- tag_batch (retry behavior) --------------------------------------------


def test_tag_batch_retries_once_on_malformed_json_then_succeeds():
    reviews = [make_review("r1")]
    rotator = KeyRotator(["k1"])
    calls = []

    def http_post(url, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return llm_response("this is not json")
        return llm_response(tag_json([("r1", "produce", "negative", ["rotten"])]))

    result = tag_batch(reviews, rotator, http_post=http_post)
    assert result["r1"]["sentiment"] == "negative"
    assert len(calls) == 2
    # second call's message history should include the correction prompt
    assert "invalid" in calls[1]["messages"][-1]["content"]


def test_tag_batch_raises_after_exhausting_retries():
    reviews = [make_review("r1")]
    rotator = KeyRotator(["k1"])
    http_post = lambda *a, **kw: llm_response("still not json")

    with pytest.raises(ValueError, match="failed to produce"):
        tag_batch(reviews, rotator, http_post=http_post, max_retries=1)


# --- tag_reviews (multi-batch, metadata merge) -----------------------------


def test_tag_reviews_batches_and_merges_original_metadata():
    reviews = [
        make_review("r1", text="Milk was spilled", rating=2),
        make_review("r2", text="Fast delivery as always", rating=5),
        make_review("r3", text="App crashed on checkout", rating=1),
    ]
    rotator = KeyRotator(["k1"])
    call_batches = []

    def http_post(url, headers, json, timeout):
        # Identify which batch this is from the prompt content.
        content = json["messages"][-1]["content"]
        ids_in_batch = [rid for rid in ("r1", "r2", "r3") if f"id={rid} " in content]
        call_batches.append(ids_in_batch)
        return llm_response(
            tag_json([(rid, "dairy" if rid == "r1" else None, "negative" if rid != "r2" else "positive", ["tag"]) for rid in ids_in_batch])
        )

    tagged = tag_reviews(reviews, rotator, batch_size=2, http_post=http_post)

    assert call_batches == [["r1", "r2"], ["r3"]]
    assert len(tagged) == 3
    by_id = {t["review_id"]: t for t in tagged}
    assert by_id["r1"]["text"] == "Milk was spilled"
    assert by_id["r1"]["rating"] == 2
    assert by_id["r1"]["category"] == "dairy"
    assert by_id["r2"]["sentiment"] == "positive"
    assert by_id["r3"]["sentiment"] == "negative"


# --- save_tagged -----------------------------------------------------------


def test_save_tagged_writes_expected_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TAGGED_DIR", tmp_path)
    from datetime import datetime, timezone

    run_date = datetime(2026, 8, 2, tzinfo=timezone.utc)
    tagged = [{"review_id": "r1", "date": "2026-07-01T00:00:00+00:00", "rating": 2, "text": "x", "category": None, "sentiment": "negative", "theme_tags": ["a"]}]

    path = save_tagged(tagged, run_date=run_date)

    assert path.name == "reviews_tagged_2026-08-02.json"
    assert '"r1"' in path.read_text()
