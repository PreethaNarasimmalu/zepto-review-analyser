"""Stage 2: bottom-up clustering of Stage 1's tags into 8-12 themes.

Parameterized by timeframe (7/30/90 days by default, see
config.TIMEFRAME_OPTIONS_DAYS): filters the one tagged pool down to a
date window rather than re-sampling, then clusters that window's tags
into themes with one LLM call. Computed lazily and cached to disk per
(run_date, timeframe) so re-selecting an already-computed window in the
UI costs zero further LLM calls.

Themes emerge from the tag data itself — the 8 research questions are
never given to the model, to avoid confirmation bias (bottom-up
taxonomy, per ARCHITECTURE.md).
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from zepto_discovery import config
from zepto_discovery.grok_client import chat_completion
from zepto_discovery.llm_utils import strip_code_fence
from zepto_discovery.util import parse_iso_datetime

REQUIRED_THEME_FIELDS = {"name", "description", "count", "example_review_ids"}


def filter_by_timeframe(tagged_reviews, timeframe_days, run_date=None):
    run_date = run_date or datetime.now(timezone.utc)
    cutoff = run_date - timedelta(days=timeframe_days)
    return [
        review
        for review in tagged_reviews
        if parse_iso_datetime(review["date"]) >= cutoff
    ]


def aggregate_tags(tagged_reviews, max_examples_per_tag=5):
    """Frequency-ranked list of raw tags, each with a few example review IDs.

    Tags are grouped by exact text (case/whitespace-insensitive) — the
    LLM does the semantic clustering into higher-level themes; this just
    keeps the prompt compact and gives it real frequency signal to work
    from, per ARCHITECTURE.md's "aggregated tags, not raw text" design.
    """
    review_ids_by_key = defaultdict(list)
    display_text = {}
    for review in tagged_reviews:
        for tag in review.get("theme_tags") or []:
            key = tag.strip().lower()
            if not key:
                continue
            display_text.setdefault(key, tag.strip())
            review_ids_by_key[key].append(review["review_id"])

    tag_list = [
        {
            "tag": display_text[key],
            "count": len(ids),
            "example_review_ids": ids[:max_examples_per_tag],
        }
        for key, ids in review_ids_by_key.items()
    ]
    tag_list.sort(key=lambda t: t["count"], reverse=True)
    return tag_list


def build_stage2_messages(tag_list):
    system = (
        "You analyze theme tags extracted from Play Store reviews of a "
        "grocery delivery app. You will be given a frequency-ranked list "
        "of raw theme tags, each with a count and a few example review "
        "IDs that tag came from. Group these raw tags into 8-12 "
        "higher-level themes that emerge naturally from the data. Do not "
        "force the data into any predetermined set of topics — let the "
        "grouping reflect what's actually there. For each theme return: "
        "a short name, a one-sentence description, the total frequency "
        "count (sum of the raw tags grouped into it), and a list of "
        "example review IDs drawn from the underlying tags' example IDs "
        "(at least 1, ideally 3-5). Return ONLY a JSON array, one object "
        'per theme, with exactly these keys: "name", "description", '
        '"count", "example_review_ids". No prose, no markdown fences.'
    )
    user = "Raw tag frequency list:\n" + json.dumps(tag_list, ensure_ascii=False)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_stage2_response(raw_text, valid_review_ids):
    try:
        parsed = json.loads(strip_code_fence(raw_text))
    except json.JSONDecodeError as e:
        raise ValueError(f"Stage 2 response was not valid JSON: {e}") from e

    if not isinstance(parsed, list) or not parsed:
        raise ValueError("Stage 2 response JSON was not a non-empty list.")

    themes = []
    for item in parsed:
        if not isinstance(item, dict) or not REQUIRED_THEME_FIELDS.issubset(item.keys()):
            raise ValueError(f"Stage 2 response item missing required fields: {item!r}")
        ids = item["example_review_ids"]
        if not isinstance(ids, list) or not ids:
            raise ValueError(f"Theme {item.get('name')!r} has no example_review_ids.")
        bogus = [rid for rid in ids if rid not in valid_review_ids]
        if bogus:
            raise ValueError(f"Theme {item.get('name')!r} cites unknown review_id(s): {bogus[:3]}")
        themes.append(item)

    return themes


def cluster_themes(
    tagged_reviews,
    rotator,
    timeframe_days,
    run_date=None,
    http_post=None,
    max_retries=1,
    max_tags=200,
    thin_window_min_reviews=None,
):
    """Runs Stage 2 for one timeframe. One LLM call (plus retries)."""
    run_date = run_date or datetime.now(timezone.utc)
    thin_window_min_reviews = (
        config.THIN_WINDOW_MIN_REVIEWS if thin_window_min_reviews is None else thin_window_min_reviews
    )

    filtered = filter_by_timeframe(tagged_reviews, timeframe_days, run_date=run_date)
    valid_ids = {r["review_id"] for r in filtered}
    tag_list = aggregate_tags(filtered)
    messages = build_stage2_messages(tag_list[:max_tags])

    themes = None
    last_error = None
    for _ in range(max_retries + 1):
        raw = chat_completion(rotator, messages, http_post=http_post)
        try:
            themes = parse_stage2_response(raw, valid_ids)
            break
        except ValueError as e:
            last_error = e
            messages = messages + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        f"That response was invalid: {e}. Return ONLY a "
                        "valid JSON array matching the required schema, "
                        "with no extra text or markdown."
                    ),
                },
            ]

    if themes is None:
        raise ValueError(
            f"Stage 2 clustering for the {timeframe_days}-day window failed to "
            f"produce valid JSON after {max_retries + 1} attempt(s): {last_error}"
        )

    return {
        "timeframe_days": timeframe_days,
        "pool_size": len(filtered),
        "is_thin": len(filtered) < thin_window_min_reviews,
        "theme_count_in_expected_range": 8 <= len(themes) <= 12,
        "themes": themes,
    }


def themes_path(run_date, timeframe_days):
    config.CLUSTERED_DIR.mkdir(parents=True, exist_ok=True)
    return config.CLUSTERED_DIR / f"themes_{run_date:%Y-%m-%d}_{timeframe_days}d.json"


def save_themes(result, run_date=None):
    run_date = run_date or datetime.now(timezone.utc)
    path = themes_path(run_date, result["timeframe_days"])
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return path


def load_themes(run_date, timeframe_days):
    path = themes_path(run_date, timeframe_days)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def get_or_compute_themes(tagged_reviews, rotator, timeframe_days, run_date=None, force=False, **kwargs):
    """Lazy, cached wrapper: returns (result, was_cached).

    Loads a previously saved result for this (run_date, timeframe_days)
    if one exists; otherwise computes it (1 LLM call) and saves it. This
    is what makes re-selecting a timeframe in the UI free after the
    first time.
    """
    run_date = run_date or datetime.now(timezone.utc)
    if not force:
        cached = load_themes(run_date, timeframe_days)
        if cached is not None:
            return cached, True

    result = cluster_themes(tagged_reviews, rotator, timeframe_days, run_date=run_date, **kwargs)
    save_themes(result, run_date=run_date)
    return result, False


if __name__ == "__main__":
    from zepto_discovery.grok_client import KeyRotator, load_api_keys

    tagged_files = sorted(config.TAGGED_DIR.glob("reviews_tagged_*.json"))
    if not tagged_files:
        raise SystemExit("No tagged review file found in data/tagged/. Run zepto_discovery.tagging first.")
    latest = tagged_files[-1]
    tagged = json.loads(latest.read_text())

    rotator = KeyRotator(load_api_keys())
    for days in config.TIMEFRAME_OPTIONS_DAYS:
        result, was_cached = get_or_compute_themes(tagged, rotator, days)
        tag = "cached" if was_cached else "computed"
        print(
            f"{days}-day window ({tag}): pool={result['pool_size']} "
            f"thin={result['is_thin']} themes={len(result['themes'])}"
        )
