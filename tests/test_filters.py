from datetime import datetime, timezone

from zepto_discovery import config
from zepto_discovery.filters import (
    filter_reviews,
    is_emoji_only,
    is_generic,
    is_rating_restatement,
    is_too_short,
    save_filtered,
    word_count,
)

# --- is_too_short --------------------------------------------------------


def test_word_count_basic():
    assert word_count("Late delivery again") == 3
    assert word_count("") == 0
    assert word_count(None) == 0


def test_too_short_under_three_words_dropped():
    assert is_too_short("ok") is True
    assert is_too_short("worst app") is True
    assert is_too_short("") is True


def test_too_short_three_words_kept():
    assert is_too_short("Late delivery again") is False


def test_too_short_substantive_short_review_still_dropped_by_word_count():
    # A 3-word floor is a blunt instrument by design — this rule only
    # checks length, other rules catch generic-but-longer reviews.
    assert is_too_short("Fast") is True


# --- is_emoji_only --------------------------------------------------------


def test_emoji_only_true_for_pure_emoji():
    assert is_emoji_only("😂😂😂") is True
    assert is_emoji_only("👍") is True
    assert is_emoji_only("🔥🔥🔥") is True


def test_emoji_only_false_when_real_words_present():
    assert is_emoji_only("Good app 👍") is False
    assert is_emoji_only("🔥 amazing delivery speed") is False


def test_emoji_only_false_for_empty_text():
    # Empty text is too_short's job, not emoji_only's.
    assert is_emoji_only("") is False


# --- is_generic -------------------------------------------------------------


def test_generic_true_for_pure_filler():
    assert is_generic("nice app") is True
    assert is_generic("very good app") is True
    assert is_generic("worst app ever") is True
    assert is_generic("amazing") is True


def test_generic_false_when_specific_content_present():
    assert is_generic("good app but delivery was late") is False
    assert is_generic("nice discounts on vegetables") is False
    assert is_generic("crashes every time I add oranges to cart") is False


# --- is_rating_restatement --------------------------------------------------


def test_rating_restatement_true_for_bare_star_mentions():
    assert is_rating_restatement("5 stars") is True
    assert is_rating_restatement("five stars") is True
    assert is_rating_restatement("1 star worst app") is True


def test_rating_restatement_false_without_star_mention():
    assert is_rating_restatement("worst app ever") is False


def test_rating_restatement_false_when_specific_content_remains():
    assert is_rating_restatement("3 stars good delivery but produce was old") is False


# --- filter_reviews (combined pipeline) --------------------------------------

SAMPLE_REVIEWS = [
    {"review_id": "r1", "text": "ok"},  # too_short
    {"review_id": "r2", "text": "😂😂😂"},  # emoji_only
    {"review_id": "r3", "text": "very good app"},  # generic
    {"review_id": "r4", "text": "5 stars"},  # rating_restatement
    {"review_id": "r5", "text": "Vegetables arrived rotten twice this week"},  # kept
    {"review_id": "r6", "text": "Only buy milk here, never tried other categories"},  # kept
]


def test_filter_reviews_drop_log_matches_expected_rule_per_review():
    kept, drop_log = filter_reviews(SAMPLE_REVIEWS)
    assert {r["review_id"] for r in kept} == {"r5", "r6"}
    assert drop_log == {
        "r1": "too_short",
        "r2": "emoji_only",
        "r3": "generic",
        "r4": "rating_restatement",
    }


def test_filter_reviews_each_review_dropped_by_at_most_one_rule():
    _, drop_log = filter_reviews(SAMPLE_REVIEWS)
    assert len(drop_log) == len(set(drop_log.keys()))


# --- save_filtered ------------------------------------------------------


def test_save_filtered_writes_both_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FILTERED_DIR", tmp_path)
    kept = [{"review_id": "r5", "text": "Vegetables arrived rotten twice this week"}]
    drop_log = {"r1": "too_short"}
    run_date = datetime(2026, 8, 2, tzinfo=timezone.utc)

    filtered_path, drop_log_path = save_filtered(kept, drop_log, run_date=run_date)

    assert filtered_path.name == "reviews_filtered_2026-08-02.json"
    assert drop_log_path.name == "drop_log_2026-08-02.json"
    assert filtered_path.parent == tmp_path
    assert '"r5"' in filtered_path.read_text()
    assert '"too_short"' in drop_log_path.read_text()
