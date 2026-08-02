import json
from datetime import datetime, timedelta, timezone

import pytest

from zepto_discovery import config
from zepto_discovery.clustering import (
    aggregate_tags,
    cluster_themes,
    filter_by_timeframe,
    get_or_compute_themes,
    parse_stage2_response,
    save_themes,
)
from zepto_discovery.grok_client import KeyRotator

RUN_DATE = datetime(2026, 8, 2, tzinfo=timezone.utc)


def days_ago(n):
    return (RUN_DATE - timedelta(days=n)).isoformat()


def make_tagged(review_id, days_old, tags, category=None, sentiment="neutral"):
    return {
        "review_id": review_id,
        "date": days_ago(days_old),
        "rating": 3,
        "text": f"review body {review_id}",
        "category": category,
        "sentiment": sentiment,
        "theme_tags": tags,
    }


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


def llm_response(content):
    return FakeResponse(200, {"choices": [{"message": {"content": content}}]})


def theme_json(themes):
    return json.dumps(themes)


# --- filter_by_timeframe --------------------------------------------------


def test_filter_by_timeframe_keeps_only_within_window():
    reviews = [
        make_tagged("r1", days_old=1, tags=["a"]),
        make_tagged("r2", days_old=6, tags=["a"]),
        make_tagged("r3", days_old=8, tags=["a"]),
    ]
    result = filter_by_timeframe(reviews, timeframe_days=7, run_date=RUN_DATE)
    assert {r["review_id"] for r in result} == {"r1", "r2"}


def test_filter_by_timeframe_boundary_is_inclusive():
    reviews = [make_tagged("r1", days_old=7, tags=["a"])]
    result = filter_by_timeframe(reviews, timeframe_days=7, run_date=RUN_DATE)
    assert {r["review_id"] for r in result} == {"r1"}


# --- aggregate_tags --------------------------------------------------------


def test_aggregate_tags_groups_case_insensitively_and_ranks_by_frequency():
    reviews = [
        make_tagged("r1", 1, ["late delivery"]),
        make_tagged("r2", 1, ["Late Delivery"]),
        make_tagged("r3", 1, ["rude staff"]),
    ]
    tag_list = aggregate_tags(reviews)
    assert tag_list[0]["tag"] == "late delivery"
    assert tag_list[0]["count"] == 2
    assert set(tag_list[0]["example_review_ids"]) == {"r1", "r2"}
    assert tag_list[1]["tag"] == "rude staff"
    assert tag_list[1]["count"] == 1


def test_aggregate_tags_caps_examples_per_tag():
    reviews = [make_tagged(f"r{i}", 1, ["common tag"]) for i in range(10)]
    tag_list = aggregate_tags(reviews, max_examples_per_tag=3)
    assert tag_list[0]["count"] == 10
    assert len(tag_list[0]["example_review_ids"]) == 3


# --- parse_stage2_response --------------------------------------------------


def test_parse_stage2_response_happy_path():
    raw = theme_json(
        [
            {"name": "Delivery speed", "description": "...", "count": 5, "example_review_ids": ["r1", "r2"]},
        ]
    )
    themes = parse_stage2_response(raw, valid_review_ids={"r1", "r2"})
    assert len(themes) == 1
    assert themes[0]["name"] == "Delivery speed"


def test_parse_stage2_response_raises_on_missing_field():
    raw = json.dumps([{"name": "x", "description": "y", "count": 1}])
    with pytest.raises(ValueError, match="missing required fields"):
        parse_stage2_response(raw, valid_review_ids={"r1"})


def test_parse_stage2_response_raises_on_empty_example_ids():
    raw = theme_json([{"name": "x", "description": "y", "count": 1, "example_review_ids": []}])
    with pytest.raises(ValueError, match="no example_review_ids"):
        parse_stage2_response(raw, valid_review_ids={"r1"})


def test_parse_stage2_response_raises_on_hallucinated_review_id():
    raw = theme_json([{"name": "x", "description": "y", "count": 1, "example_review_ids": ["r999"]}])
    with pytest.raises(ValueError, match="unknown review_id"):
        parse_stage2_response(raw, valid_review_ids={"r1"})


def test_parse_stage2_response_raises_on_empty_list():
    with pytest.raises(ValueError, match="non-empty list"):
        parse_stage2_response("[]", valid_review_ids={"r1"})


# --- cluster_themes (retry + thin-window guard) -----------------------------


def _twelve_themes(ids):
    return [
        {"name": f"theme {i}", "description": "d", "count": 1, "example_review_ids": ids}
        for i in range(12)
    ]


def test_cluster_themes_retries_once_on_malformed_then_succeeds():
    reviews = [make_tagged("r1", 1, ["a"])]
    rotator = KeyRotator(["k1"])
    calls = []

    def http_post(url, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return llm_response("not json")
        return llm_response(theme_json(_twelve_themes(["r1"])))

    result = cluster_themes(reviews, rotator, timeframe_days=90, run_date=RUN_DATE, http_post=http_post)
    assert len(calls) == 2
    assert len(result["themes"]) == 12
    assert result["theme_count_in_expected_range"] is True


def test_cluster_themes_marks_thin_window():
    reviews = [make_tagged(f"r{i}", 1, ["a"]) for i in range(5)]
    rotator = KeyRotator(["k1"])
    ids = [r["review_id"] for r in reviews]
    http_post = lambda *a, **kw: llm_response(theme_json(_twelve_themes(ids[:1])))

    result = cluster_themes(reviews, rotator, timeframe_days=7, run_date=RUN_DATE, http_post=http_post)
    assert result["pool_size"] == 5
    assert result["is_thin"] is True


def test_cluster_themes_raises_after_exhausting_retries():
    reviews = [make_tagged("r1", 1, ["a"])]
    rotator = KeyRotator(["k1"])
    http_post = lambda *a, **kw: llm_response("still not json")

    with pytest.raises(ValueError, match="failed to produce valid JSON"):
        cluster_themes(reviews, rotator, timeframe_days=90, run_date=RUN_DATE, http_post=http_post, max_retries=1)


def test_cluster_themes_only_filters_before_clustering_out_of_window_review_cant_be_cited():
    in_window = make_tagged("r1", 1, ["a"])
    out_of_window = make_tagged("r2", 100, ["a"])
    rotator = KeyRotator(["k1"])
    # Model tries to cite an out-of-window review — should be rejected as unknown.
    http_post = lambda *a, **kw: llm_response(theme_json(_twelve_themes(["r2"])))

    with pytest.raises(ValueError, match="unknown review_id"):
        cluster_themes(
            [in_window, out_of_window], rotator, timeframe_days=7, run_date=RUN_DATE, http_post=http_post
        )


# --- get_or_compute_themes (lazy + cached) ---------------------------------


def test_get_or_compute_themes_computes_once_then_reuses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CLUSTERED_DIR", tmp_path)
    reviews = [make_tagged("r1", 1, ["a"])]
    rotator = KeyRotator(["k1"])
    call_count = {"n": 0}

    def http_post(url, headers, json, timeout):
        call_count["n"] += 1
        return llm_response(theme_json(_twelve_themes(["r1"])))

    result1, cached1 = get_or_compute_themes(
        reviews, rotator, timeframe_days=90, run_date=RUN_DATE, http_post=http_post
    )
    result2, cached2 = get_or_compute_themes(
        reviews, rotator, timeframe_days=90, run_date=RUN_DATE, http_post=http_post
    )

    assert cached1 is False
    assert cached2 is True
    assert call_count["n"] == 1  # second call made zero new LLM calls
    assert result1 == result2


def test_get_or_compute_themes_force_recomputes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CLUSTERED_DIR", tmp_path)
    reviews = [make_tagged("r1", 1, ["a"])]
    rotator = KeyRotator(["k1"])
    call_count = {"n": 0}

    def http_post(url, headers, json, timeout):
        call_count["n"] += 1
        return llm_response(theme_json(_twelve_themes(["r1"])))

    get_or_compute_themes(reviews, rotator, timeframe_days=90, run_date=RUN_DATE, http_post=http_post)
    _, cached = get_or_compute_themes(
        reviews, rotator, timeframe_days=90, run_date=RUN_DATE, http_post=http_post, force=True
    )

    assert cached is False
    assert call_count["n"] == 2


def test_different_timeframes_are_cached_independently(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CLUSTERED_DIR", tmp_path)
    reviews = [make_tagged("r1", 1, ["a"])]
    rotator = KeyRotator(["k1"])
    http_post = lambda *a, **kw: llm_response(theme_json(_twelve_themes(["r1"])))

    _, cached_90 = get_or_compute_themes(reviews, rotator, timeframe_days=90, run_date=RUN_DATE, http_post=http_post)
    _, cached_7 = get_or_compute_themes(reviews, rotator, timeframe_days=7, run_date=RUN_DATE, http_post=http_post)

    assert cached_90 is False
    assert cached_7 is False  # different timeframe, not yet cached


# --- save_themes / load_themes --------------------------------------------


def test_save_themes_writes_expected_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CLUSTERED_DIR", tmp_path)
    result = {"timeframe_days": 30, "pool_size": 1, "is_thin": True, "theme_count_in_expected_range": False, "themes": []}
    path = save_themes(result, run_date=RUN_DATE)
    assert path.name == "themes_2026-08-02_30d.json"
