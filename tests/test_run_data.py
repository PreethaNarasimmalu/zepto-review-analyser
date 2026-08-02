import json
from datetime import datetime, timezone

from zepto_discovery import config
from zepto_discovery.grok_client import KeyRotator
from zepto_discovery.run_data import (
    ensure_timeframe_computed,
    funnel_counts,
    latest_run_date,
    load_tagged_reviews,
    load_timeframe_view,
)

RUN_DATE = datetime(2026, 8, 2, tzinfo=timezone.utc)
OLDER_DATE = datetime(2026, 7, 20, tzinfo=timezone.utc)


def _write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records))


# --- latest_run_date --------------------------------------------------------


def test_latest_run_date_none_when_no_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TAGGED_DIR", tmp_path)
    assert latest_run_date() is None


def test_latest_run_date_picks_most_recent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TAGGED_DIR", tmp_path)
    _write(tmp_path / f"reviews_tagged_{OLDER_DATE:%Y-%m-%d}.json", [])
    _write(tmp_path / f"reviews_tagged_{RUN_DATE:%Y-%m-%d}.json", [])
    assert latest_run_date() == RUN_DATE


# --- funnel_counts -----------------------------------------------------------


def test_funnel_counts_reads_each_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(config, "FILTERED_DIR", tmp_path / "filtered")
    monkeypatch.setattr(config, "SAMPLED_DIR", tmp_path / "sampled")
    monkeypatch.setattr(config, "TAGGED_DIR", tmp_path / "tagged")

    _write(config.RAW_DIR / f"reviews_raw_{RUN_DATE:%Y-%m-%d}.json", [1, 2, 3, 4])
    _write(config.FILTERED_DIR / f"reviews_filtered_{RUN_DATE:%Y-%m-%d}.json", [1, 2, 3])
    _write(config.SAMPLED_DIR / f"reviews_sampled_{RUN_DATE:%Y-%m-%d}.json", [1, 2])
    _write(config.TAGGED_DIR / f"reviews_tagged_{RUN_DATE:%Y-%m-%d}.json", [1, 2])

    counts = funnel_counts(RUN_DATE)
    assert counts == {"scraped": 4, "filtered": 3, "sampled": 2, "tagged": 2}


def test_funnel_counts_none_for_missing_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(config, "FILTERED_DIR", tmp_path / "filtered")
    monkeypatch.setattr(config, "SAMPLED_DIR", tmp_path / "sampled")
    monkeypatch.setattr(config, "TAGGED_DIR", tmp_path / "tagged")

    _write(config.TAGGED_DIR / f"reviews_tagged_{RUN_DATE:%Y-%m-%d}.json", [1])

    counts = funnel_counts(RUN_DATE)
    assert counts["scraped"] is None
    assert counts["tagged"] == 1


# --- load_tagged_reviews / load_timeframe_view ------------------------------


def test_load_tagged_reviews_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TAGGED_DIR", tmp_path)
    assert load_tagged_reviews(RUN_DATE) is None


def test_load_tagged_reviews_returns_records(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TAGGED_DIR", tmp_path)
    _write(tmp_path / f"reviews_tagged_{RUN_DATE:%Y-%m-%d}.json", [{"review_id": "r1"}])
    assert load_tagged_reviews(RUN_DATE) == [{"review_id": "r1"}]


def test_load_timeframe_view_none_when_not_computed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CLUSTERED_DIR", tmp_path / "clustered")
    monkeypatch.setattr(config, "SYNTHESIS_DIR", tmp_path / "synthesis")
    view = load_timeframe_view(RUN_DATE, 90)
    assert view == {"themes": None, "answers": None}


# --- ensure_timeframe_computed -----------------------------------------------


class FakeResponse:
    def __init__(self, content):
        self.status_code = 200
        self.text = ""
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _twelve_themes(ids):
    return [
        {"name": f"theme {i}", "description": "d", "count": 1, "example_review_ids": ids}
        for i in range(12)
    ]


def _eight_answers(ids):
    from zepto_discovery.synthesis import RESEARCH_QUESTIONS

    return [
        {
            "question": q,
            "answer": "a",
            "supporting_reviews": [{"review_id": rid, "excerpt": "e", "theme": "t"} for rid in ids],
        }
        for q in RESEARCH_QUESTIONS
    ]


def test_ensure_timeframe_computed_computes_both_then_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CLUSTERED_DIR", tmp_path / "clustered")
    monkeypatch.setattr(config, "SYNTHESIS_DIR", tmp_path / "synthesis")

    tagged = [
        {
            "review_id": f"r{i}",
            "date": RUN_DATE.isoformat(),
            "rating": 3,
            "text": "x",
            "sentiment": "neutral",
            "theme_tags": ["a"],
        }
        for i in range(3)
    ]
    rotator = KeyRotator(["k1"])
    call_count = {"n": 0}

    def http_post(url, headers, json, timeout):
        call_count["n"] += 1
        content = json["messages"][-1]["content"]
        if "Clustered themes" in content:
            return FakeResponse(__import__("json").dumps(_eight_answers(["r0", "r1", "r2"])))
        return FakeResponse(__import__("json").dumps(_twelve_themes(["r0", "r1", "r2"])))

    themes1, answers1, computed1 = ensure_timeframe_computed(RUN_DATE, 90, tagged, rotator, http_post=http_post)
    assert computed1 is True
    assert call_count["n"] == 2  # one clustering call, one synthesis call

    themes2, answers2, computed2 = ensure_timeframe_computed(RUN_DATE, 90, tagged, rotator, http_post=http_post)
    assert computed2 is False
    assert call_count["n"] == 2  # no new calls on cache hit
    assert themes1 == themes2
    assert answers1 == answers2
