"""Phase 10: ad-hoc re-query against already-tagged/clustered data.

Answers one free-text question using the same evidence-linked
{question, answer, supporting_reviews} format as Stage 3 — one LLM call
per question, scoped to whichever timeframe's clustered themes are
passed in, without re-running scraping, filtering, sampling, tagging,
or clustering. This is exactly why Phase 4's tagged-review schema was
designed to be self-describing/queryable from the start.
"""

import json
from datetime import datetime, timezone

from zepto_discovery.clustering import filter_by_timeframe
from zepto_discovery.grok_client import chat_completion
from zepto_discovery.llm_utils import strip_code_fence
from zepto_discovery.synthesis import (
    MAX_SUPPORTING,
    MIN_SUPPORTING,
    REQUIRED_SUPPORT_FIELDS,
    theme_blocks,
)

REQUIRED_ANSWER_FIELDS = {"answer", "supporting_reviews"}


def build_requery_messages(question, themes, tagged_reviews):
    system = (
        "You are analyzing clustered review themes from a grocery delivery "
        "app to answer one specific ad-hoc question. Base your answer "
        "ONLY on the theme data and example reviews given below — do not "
        "invent facts or reviews. Answer concisely (2-4 sentences) and "
        "cite between 3 and 5 supporting reviews drawn ONLY from the "
        "review_ids provided below, each with its given excerpt and the "
        "theme it came from. Return ONLY a JSON object with exactly these "
        'keys: "answer", "supporting_reviews" (a list of 3 to 5 objects '
        'with "review_id", "excerpt", "theme"). No prose, no markdown fences.'
    )
    user = (
        "Clustered themes:\n"
        + json.dumps(theme_blocks(themes, tagged_reviews), ensure_ascii=False)
        + f"\n\nQuestion: {question}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_requery_response(raw_text, valid_review_ids):
    try:
        parsed = json.loads(strip_code_fence(raw_text))
    except json.JSONDecodeError as e:
        raise ValueError(f"Re-query response was not valid JSON: {e}") from e

    if not isinstance(parsed, dict) or not REQUIRED_ANSWER_FIELDS.issubset(parsed.keys()):
        raise ValueError(f"Re-query response missing required fields: {parsed!r}")

    supports = parsed["supporting_reviews"]
    if not isinstance(supports, list):
        raise ValueError("supporting_reviews was not a list.")
    if not (MIN_SUPPORTING <= len(supports) <= MAX_SUPPORTING):
        raise ValueError(
            f"Re-query answer has {len(supports)} supporting reviews; "
            f"expected {MIN_SUPPORTING}-{MAX_SUPPORTING}."
        )
    for support in supports:
        if not isinstance(support, dict) or not REQUIRED_SUPPORT_FIELDS.issubset(support.keys()):
            raise ValueError(f"Malformed supporting_review entry: {support!r}")
        if support["review_id"] not in valid_review_ids:
            raise ValueError(
                f"Re-query answer cites unknown review_id {support['review_id']!r}"
            )

    return parsed


def answer_question(
    question, themes_result, tagged_reviews, rotator, run_date=None, http_post=None, max_retries=1
):
    """Answers one ad-hoc question against themes_result's timeframe.

    Exactly one LLM call (plus retries) — no earlier phase is re-run.
    Raises before calling the LLM if the in-window pool can't possibly
    satisfy the 3-5-citation requirement, same guard as synthesize().
    """
    run_date = run_date or datetime.now(timezone.utc)
    timeframe_days = themes_result["timeframe_days"]

    in_window = filter_by_timeframe(tagged_reviews, timeframe_days, run_date=run_date)
    valid_ids = {r["review_id"] for r in in_window}

    if len(valid_ids) < MIN_SUPPORTING:
        raise ValueError(
            f"Only {len(valid_ids)} tagged review(s) fall inside the "
            f"{timeframe_days}-day window — not enough to satisfy the "
            f"{MIN_SUPPORTING}-{MAX_SUPPORTING} supporting-review "
            "requirement. Skipping the LLM call rather than risking "
            "fabricated evidence."
        )

    messages = build_requery_messages(question, themes_result["themes"], in_window)

    answer = None
    last_error = None
    for _ in range(max_retries + 1):
        raw = chat_completion(rotator, messages, http_post=http_post)
        try:
            answer = parse_requery_response(raw, valid_ids)
            break
        except ValueError as e:
            last_error = e
            messages = messages + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        f"That response was invalid: {e}. Return ONLY a "
                        "valid JSON object matching the required schema, "
                        "with no extra text or markdown."
                    ),
                },
            ]

    if answer is None:
        raise ValueError(
            f"Re-query for {question!r} failed to produce valid JSON after "
            f"{max_retries + 1} attempt(s): {last_error}"
        )

    return {
        "question": question,
        "answer": answer["answer"],
        "supporting_reviews": answer["supporting_reviews"],
    }
