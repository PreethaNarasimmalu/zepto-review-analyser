"""Stage 1: batched LLM tagging of sampled reviews.

Per review, extracts the product category mentioned, sentiment, and raw
theme tags close to the reviewer's own language. Output is designed to be
re-queryable later (Phase 8's timeframe selector, Phase 10's ad-hoc
questions) without re-running this stage.
"""

import json
from datetime import datetime, timezone

from zepto_discovery import config
from zepto_discovery.grok_client import chat_completion

REQUIRED_FIELDS = {"review_id", "category", "sentiment", "theme_tags"}
VALID_SENTIMENTS = {"positive", "negative", "neutral", "mixed"}


def batch_reviews(reviews, batch_size=None):
    batch_size = config.STAGE1_BATCH_SIZE if batch_size is None else batch_size
    return [reviews[i : i + batch_size] for i in range(0, len(reviews), batch_size)]


def build_stage1_messages(batch):
    review_lines = "\n".join(
        f'- id={r["review_id"]} | rating={r.get("rating")} | text="{r["text"]}"'
        for r in batch
    )
    system = (
        "You tag app store reviews for a grocery delivery app. For EACH "
        "review given, extract: the product category mentioned (if any, "
        "else null), the sentiment (one of: positive, negative, neutral, "
        "mixed), and 2-5 short raw theme tags close to the reviewer's own "
        "words (not abstract categories). Return ONLY a JSON array, one "
        'object per review, with exactly these keys: "review_id", '
        '"category", "sentiment", "theme_tags" (a list of strings). No '
        "prose, no markdown fences."
    )
    user = f"Reviews:\n{review_lines}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _strip_code_fence(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return text.strip()


def parse_stage1_response(raw_text, expected_ids):
    """Parses and validates the model's JSON array against expected_ids.

    Raises ValueError describing what's wrong, rather than letting a raw
    JSONDecodeError/KeyError leak out — the caller uses this to decide
    whether to retry.
    """
    try:
        parsed = json.loads(_strip_code_fence(raw_text))
    except json.JSONDecodeError as e:
        raise ValueError(f"Stage 1 response was not valid JSON: {e}") from e

    if not isinstance(parsed, list):
        raise ValueError("Stage 1 response JSON was not a list.")

    by_id = {}
    for item in parsed:
        if not isinstance(item, dict) or not REQUIRED_FIELDS.issubset(item.keys()):
            raise ValueError(f"Stage 1 response item missing required fields: {item!r}")
        if item["sentiment"] not in VALID_SENTIMENTS:
            raise ValueError(f"Unexpected sentiment value: {item['sentiment']!r}")
        if not isinstance(item["theme_tags"], list):
            raise ValueError(
                f"theme_tags was not a list for review {item.get('review_id')!r}"
            )
        by_id[item["review_id"]] = item

    missing = expected_ids - by_id.keys()
    if missing:
        raise ValueError(
            f"Stage 1 response is missing {len(missing)} expected review_id(s): "
            f"{sorted(missing)[:5]}"
        )

    return by_id


def tag_batch(batch, rotator, http_post=None, max_retries=1):
    """Tags one batch, retrying once (by default) with a stricter reminder
    appended to the conversation if the response fails to parse/validate."""
    expected_ids = {r["review_id"] for r in batch}
    messages = build_stage1_messages(batch)

    last_error = None
    for _ in range(max_retries + 1):
        raw = chat_completion(rotator, messages, http_post=http_post)
        try:
            return parse_stage1_response(raw, expected_ids)
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

    raise ValueError(
        f"Batch with review_ids {sorted(expected_ids)[:5]} failed to produce "
        f"valid JSON after {max_retries + 1} attempt(s): {last_error}"
    )


def tag_reviews(sampled_reviews, rotator, batch_size=None, http_post=None):
    """Tags every review, merging Stage 1 output with original metadata.

    Returns tagged records: {review_id, date, rating, text, category,
    sentiment, theme_tags} — the schema Phases 5/6/8/10 read from.
    """
    by_review_id = {r["review_id"]: r for r in sampled_reviews}
    tagged = []
    for batch in batch_reviews(sampled_reviews, batch_size=batch_size):
        results = tag_batch(batch, rotator, http_post=http_post)
        for review_id, tags in results.items():
            original = by_review_id[review_id]
            tagged.append(
                {
                    "review_id": review_id,
                    "date": original.get("date"),
                    "rating": original.get("rating"),
                    "text": original.get("text"),
                    "category": tags["category"],
                    "sentiment": tags["sentiment"],
                    "theme_tags": tags["theme_tags"],
                }
            )
    return tagged


def save_tagged(tagged, run_date=None):
    run_date = run_date or datetime.now(timezone.utc)
    config.TAGGED_DIR.mkdir(parents=True, exist_ok=True)
    path = config.TAGGED_DIR / f"reviews_tagged_{run_date:%Y-%m-%d}.json"
    path.write_text(json.dumps(tagged, indent=2, ensure_ascii=False))
    return path


if __name__ == "__main__":
    from zepto_discovery.grok_client import KeyRotator, load_api_keys

    sampled_files = sorted(config.SAMPLED_DIR.glob("reviews_sampled_*.json"))
    if not sampled_files:
        raise SystemExit("No sampled review file found in data/sampled/. Run zepto_discovery.sampler first.")
    latest = sampled_files[-1]
    sampled = json.loads(latest.read_text())

    rotator = KeyRotator(load_api_keys())
    tagged_reviews = tag_reviews(sampled, rotator)
    path = save_tagged(tagged_reviews)
    print(f"Tagged {len(tagged_reviews)}/{len(sampled)} reviews -> {path}")
