from datetime import datetime, timedelta, timezone

import pytest

from zepto_discovery import config
from zepto_discovery.scraper import save_raw, scrape_reviews

RUN_DATE = datetime(2026, 8, 2, tzinfo=timezone.utc)


def make_review(review_id, days_old, score=4, thumbs_up=0):
    return {
        "reviewId": review_id,
        "content": f"review text {review_id}",
        "score": score,
        "at": RUN_DATE - timedelta(days=days_old),
        "thumbsUpCount": thumbs_up,
    }


def paged_fetcher(pages):
    """Builds a fetch_page stand-in that returns one page per call."""
    state = {"i": 0}

    def fetch_page(app_id, lang, country, sort, count, continuation_token):
        i = state["i"]
        state["i"] += 1
        if i >= len(pages):
            return [], None
        page = pages[i]
        next_token = "more" if i < len(pages) - 1 else None
        return page, next_token

    return fetch_page


def test_single_page_entirely_within_window():
    pages = [[make_review("r1", 5), make_review("r2", 10)]]
    result = scrape_reviews(
        run_date=RUN_DATE, window_days=90, fetch_page=paged_fetcher(pages)
    )
    assert {r["review_id"] for r in result} == {"r1", "r2"}


def test_stops_once_a_page_crosses_the_boundary():
    pages = [
        [make_review("r1", 10), make_review("r2", 20)],
        [make_review("r3", 89), make_review("r4", 95)],  # r4 is past the 90-day cutoff
        [make_review("r5", 120)],  # should never be fetched
    ]
    result = scrape_reviews(
        run_date=RUN_DATE, window_days=90, fetch_page=paged_fetcher(pages)
    )
    assert {r["review_id"] for r in result} == {"r1", "r2", "r3"}


def test_review_exactly_at_the_boundary_is_kept():
    pages = [[make_review("r1", 90)]]
    result = scrape_reviews(
        run_date=RUN_DATE, window_days=90, fetch_page=paged_fetcher(pages)
    )
    assert {r["review_id"] for r in result} == {"r1"}


def test_review_one_day_past_the_boundary_is_dropped():
    pages = [[make_review("r1", 91)]]
    result = scrape_reviews(
        run_date=RUN_DATE, window_days=90, fetch_page=paged_fetcher(pages)
    )
    assert result == []


def test_empty_first_page_raises_instead_of_silently_returning_nothing():
    # google_play_scraper is known to swallow network/parsing errors and
    # return an empty list; an empty *first* page must surface as an error,
    # not a quiet zero-review result.
    with pytest.raises(RuntimeError):
        scrape_reviews(run_date=RUN_DATE, window_days=90, fetch_page=paged_fetcher([]))


def test_empty_page_after_first_is_a_normal_stop():
    pages = [[make_review("r1", 5)], []]
    result = scrape_reviews(
        run_date=RUN_DATE, window_days=90, fetch_page=paged_fetcher(pages)
    )
    assert {r["review_id"] for r in result} == {"r1"}


def test_normalized_record_shape():
    pages = [[make_review("r1", 1, score=5, thumbs_up=3)]]
    result = scrape_reviews(
        run_date=RUN_DATE, window_days=90, fetch_page=paged_fetcher(pages)
    )
    record = result[0]
    assert record.keys() == {"review_id", "text", "rating", "date", "thumbs_up"}
    assert record["rating"] == 5
    assert record["thumbs_up"] == 3
    assert "author" not in record


def test_save_raw_writes_expected_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    records = [{"review_id": "r1", "text": "ok", "rating": 5, "date": "2026-08-01T00:00:00+00:00", "thumbs_up": 0}]
    path = save_raw(records, run_date=RUN_DATE)
    assert path.exists()
    assert path.parent == tmp_path
    assert path.name == "reviews_raw_2026-08-02.json"
