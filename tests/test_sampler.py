from datetime import datetime, timedelta, timezone

from zepto_discovery import config
from zepto_discovery.sampler import recency_chunk, sample_reviews, save_sampled

RUN_DATE = datetime(2026, 8, 2, tzinfo=timezone.utc)


def days_ago(n):
    return RUN_DATE - timedelta(days=n)


def make_review(review_id, rating, days_old, text="substantive review text here"):
    return {
        "review_id": review_id,
        "text": text,
        "rating": rating,
        "date": days_ago(days_old).isoformat(),
        "thumbs_up": 0,
    }


# --- recency_chunk -----------------------------------------------------


def test_recency_chunk_boundaries_for_90_day_window_3_chunks():
    assert recency_chunk(days_ago(0), RUN_DATE) == 0
    assert recency_chunk(days_ago(29), RUN_DATE) == 0
    assert recency_chunk(days_ago(30), RUN_DATE) == 1
    assert recency_chunk(days_ago(59), RUN_DATE) == 1
    assert recency_chunk(days_ago(60), RUN_DATE) == 2
    assert recency_chunk(days_ago(89), RUN_DATE) == 2


def test_recency_chunk_clamps_past_the_window():
    assert recency_chunk(days_ago(90), RUN_DATE) == 2
    assert recency_chunk(days_ago(200), RUN_DATE) == 2


# --- sample_reviews: pool under cap --------------------------------------


def test_pool_under_cap_returns_everything():
    reviews = [make_review(f"r{i}", rating=(i % 5) + 1, days_old=i) for i in range(20)]
    sampled, report = sample_reviews(reviews, cap=1200, run_date=RUN_DATE)

    assert report["used_full_pool"] is True
    assert report["total_eligible"] == 20
    assert report["total_sampled"] == 20
    assert {r["review_id"] for r in sampled} == {r["review_id"] for r in reviews}
    for stratum in report["strata"]:
        assert stratum["sampled_count"] == stratum["eligible_count"]


# --- sample_reviews: pool over cap, proportional allocation --------------


def test_over_cap_preserves_proportions_and_hits_cap_exactly():
    # Two strata: 100 five-star/recent reviews, 50 one-star/recent reviews.
    reviews = [make_review(f"five_{i}", rating=5, days_old=1) for i in range(100)]
    reviews += [make_review(f"one_{i}", rating=1, days_old=1) for i in range(50)]

    sampled, report = sample_reviews(reviews, cap=30, run_date=RUN_DATE)

    assert report["used_full_pool"] is False
    assert report["total_sampled"] == 30
    counts = {s["rating"]: s["sampled_count"] for s in report["strata"]}
    # 100:50 pool -> roughly 2:1 sampled split at cap 30 -> 20:10
    assert counts[5] == 20
    assert counts[1] == 10


def test_never_oversamples_the_minority_stratum():
    reviews = [make_review(f"five_{i}", rating=5, days_old=1) for i in range(90)]
    reviews += [make_review(f"one_{i}", rating=1, days_old=1) for i in range(10)]

    _, report = sample_reviews(reviews, cap=20, run_date=RUN_DATE)
    counts = {s["rating"]: s["sampled_count"] for s in report["strata"]}

    # proportional (18:2), not artificially balanced toward the negative reviews
    assert counts[5] == 18
    assert counts[1] == 2


def test_sampling_is_deterministic_for_a_fixed_seed():
    reviews = [make_review(f"r{i}", rating=(i % 5) + 1, days_old=i % 90) for i in range(300)]

    sampled_a, _ = sample_reviews(reviews, cap=50, run_date=RUN_DATE, seed=7)
    sampled_b, _ = sample_reviews(reviews, cap=50, run_date=RUN_DATE, seed=7)

    assert [r["review_id"] for r in sampled_a] == [r["review_id"] for r in sampled_b]


def test_different_seeds_can_produce_different_samples():
    reviews = [make_review(f"r{i}", rating=(i % 5) + 1, days_old=i % 90) for i in range(300)]

    sampled_a, _ = sample_reviews(reviews, cap=50, run_date=RUN_DATE, seed=1)
    sampled_b, _ = sample_reviews(reviews, cap=50, run_date=RUN_DATE, seed=2)

    assert [r["review_id"] for r in sampled_a] != [r["review_id"] for r in sampled_b]


# --- save_sampled --------------------------------------------------------


def test_sample_reviews_uses_custom_window_days_for_recency_bucketing():
    # A review 6 days old should land in the *last* chunk of a 7-day
    # window (chunk_size ~2.33), but in chunk 0 of the default 90-day
    # window (chunk_size 30) - confirms window_days actually threads
    # through sample_reviews rather than silently assuming 90 days.
    reviews = [make_review("r1", rating=5, days_old=6)]

    _, report_90 = sample_reviews(reviews, cap=1200, run_date=RUN_DATE)
    _, report_7 = sample_reviews(reviews, cap=1200, run_date=RUN_DATE, window_days=7)

    chunk_90 = report_90["strata"][0]["recency_chunk"]
    chunk_7 = report_7["strata"][0]["recency_chunk"]

    assert chunk_90 == 0
    assert chunk_7 == 2
    assert chunk_90 != chunk_7


def test_save_sampled_writes_both_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAMPLED_DIR", tmp_path)
    sampled = [make_review("r1", rating=5, days_old=1)]
    report = {"total_eligible": 1, "total_sampled": 1, "cap": 1200, "used_full_pool": True, "strata": []}

    sampled_path, report_path = save_sampled(sampled, report, run_date=RUN_DATE)

    assert sampled_path.name == "reviews_sampled_2026-08-02.json"
    assert report_path.name == "sample_report_2026-08-02.json"
    assert '"r1"' in sampled_path.read_text()
    assert '"used_full_pool": true' in report_path.read_text()
