"""Pre-LLM filtering rules for scraped reviews — pure Python, zero LLM calls.

Each rule is independently testable and reports its own drop reason, so the
funnel from raw to filtered reviews stays auditable (see save_filtered's
drop log) instead of being a single opaque pass/fail.
"""

import json
import re
from datetime import datetime, timezone

from zepto_discovery import config

_WORD_RE = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002B00-\U00002BFF"
    "\U0000FE0F"
    "\U0000200D"
    "]+",
    re.UNICODE,
)

_STAR_RE = re.compile(
    r"\b(\d+|one|two|three|four|five)\s*stars?\b", re.IGNORECASE
)

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "it", "its", "it's", "this",
    "that", "very", "too", "so", "and", "but", "or", "of", "to", "for", "in",
    "on", "i", "my", "not", "no", "yes", "really", "just", "also", "ever",
}

_GENERIC_ADJECTIVES = {
    "nice", "good", "great", "best", "awesome", "excellent", "poor", "bad",
    "worst", "terrible", "pathetic", "superb", "fantastic", "wonderful",
    "amazing", "super", "cool", "ok", "okay", "fine", "horrible", "useless",
    "perfect", "love", "loved", "like", "liked", "hate", "hated",
}

_GENERIC_NOUNS = {"app", "application", "service", "product", "zepto"}


def word_count(text):
    return len(_WORD_RE.findall(text or ""))


def is_too_short(text, min_words=None):
    min_words = config.MIN_REVIEW_WORD_COUNT if min_words is None else min_words
    return word_count(text) < min_words


def is_emoji_only(text):
    text = text or ""
    if not text.strip():
        return False
    stripped = _EMOJI_RE.sub("", text)
    return not any(ch.isalnum() for ch in stripped)


def _tokens(text):
    return [w.lower() for w in _WORD_RE.findall(text or "")]


def _has_specific_content(text):
    content = [
        t
        for t in _tokens(text)
        if t not in _STOPWORDS
        and t not in _GENERIC_ADJECTIVES
        and t not in _GENERIC_NOUNS
    ]
    return len(content) > 0


def is_generic(text):
    return not _has_specific_content(text)


def is_rating_restatement(text):
    text = text or ""
    if not _STAR_RE.search(text):
        return False
    remaining = _STAR_RE.sub(" ", text)
    return not _has_specific_content(remaining)


# Checked in order; a review is dropped by the first rule that matches.
# emoji_only/rating_restatement are checked before too_short so a 1-2 word
# emoji dump or bare "5 stars" is logged under its more specific, more
# useful-to-audit reason rather than the catch-all "too_short".
DROP_RULES = (
    ("emoji_only", is_emoji_only),
    ("rating_restatement", is_rating_restatement),
    ("too_short", is_too_short),
    ("generic", is_generic),
)


def filter_reviews(reviews):
    """Applies the drop rules in order.

    Returns (kept, drop_log) where drop_log maps review_id -> the name of
    the rule that dropped it (each review is dropped by at most one rule).
    """
    kept = []
    drop_log = {}
    for review in reviews:
        text = review.get("text", "")
        review_id = review.get("review_id")
        dropped_by = next(
            (name for name, rule in DROP_RULES if rule(text)), None
        )
        if dropped_by:
            drop_log[review_id] = dropped_by
        else:
            kept.append(review)
    return kept, drop_log


def save_filtered(kept, drop_log, run_date=None):
    run_date = run_date or datetime.now(timezone.utc)
    config.FILTERED_DIR.mkdir(parents=True, exist_ok=True)
    filtered_path = config.FILTERED_DIR / f"reviews_filtered_{run_date:%Y-%m-%d}.json"
    drop_log_path = config.FILTERED_DIR / f"drop_log_{run_date:%Y-%m-%d}.json"
    filtered_path.write_text(json.dumps(kept, indent=2, ensure_ascii=False))
    drop_log_path.write_text(json.dumps(drop_log, indent=2, ensure_ascii=False))
    return filtered_path, drop_log_path


if __name__ == "__main__":
    raw_files = sorted(config.RAW_DIR.glob("reviews_raw_*.json"))
    if not raw_files:
        raise SystemExit("No raw review file found in data/raw/. Run zepto_discovery.scraper first.")
    latest = raw_files[-1]
    raw_reviews = json.loads(latest.read_text())
    kept_reviews, log = filter_reviews(raw_reviews)
    filtered_path, drop_log_path = save_filtered(kept_reviews, log)
    print(f"Kept {len(kept_reviews)}/{len(raw_reviews)} reviews -> {filtered_path}")
    print(f"Drop log -> {drop_log_path}")
