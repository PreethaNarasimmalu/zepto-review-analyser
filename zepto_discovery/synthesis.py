"""Stage 3: synthesis — answers the 8 fixed research questions from
Phase 5's clustered themes, with every answer required to cite 3-5 real
supporting reviews so the validation requirement in ARCHITECTURE.md is
enforced by the schema itself, not left as an unchecked assertion.

Same timeframe parameterization and lazy-cache pattern as Phase 5: one
call per timeframe, computed on first request and cached.
"""

import json
from datetime import datetime, timezone

from zepto_discovery import config
from zepto_discovery.clustering import filter_by_timeframe
from zepto_discovery.grok_client import chat_completion
from zepto_discovery.llm_utils import strip_code_fence

RESEARCH_QUESTIONS = [
    "Why do users repeatedly buy from the same categories?",
    "What prevents users from exploring new categories?",
    "How do users discover products today?",
    "What role do habits play in shopping behavior?",
    "What information do users need before trying a new category?",
    "What frustrations emerge repeatedly?",
    "Which user segments are more likely to experiment?",
    "What unmet needs emerge consistently across discussions?",
]

REQUIRED_ANSWER_FIELDS = {"question", "answer", "supporting_reviews"}
REQUIRED_SUPPORT_FIELDS = {"review_id", "excerpt", "theme"}
MIN_SUPPORTING = 3
MAX_SUPPORTING = 5
EXCERPT_LENGTH = 160


def theme_blocks(themes, tagged_reviews):
    by_id = {r["review_id"]: r for r in tagged_reviews}
    blocks = []
    for theme in themes:
        examples = []
        for rid in theme["example_review_ids"]:
            review = by_id.get(rid)
            if review is None:
                continue
            examples.append({"review_id": rid, "excerpt": review["text"][:EXCERPT_LENGTH]})
        blocks.append(
            {
                "name": theme["name"],
                "description": theme["description"],
                "count": theme["count"],
                "example_reviews": examples,
            }
        )
    return blocks


def build_stage3_messages(themes, tagged_reviews):
    system = (
        "You are analyzing clustered review themes from a grocery delivery "
        "app to answer research questions about category-exploration "
        "behavior. Base every answer ONLY on the theme data and example "
        "reviews given below — do not invent facts or reviews. For EACH "
        "question, answer concisely (2-4 sentences) and cite between 3 and "
        "5 supporting reviews drawn ONLY from the review_ids provided "
        "below, each with its given excerpt and the theme it came from. "
        "Return ONLY a JSON array, one object per question, in the same "
        'order as the questions given, with exactly these keys: "question", '
        '"answer", "supporting_reviews" (a list of 3 to 5 objects with '
        '"review_id", "excerpt", "theme"). No prose, no markdown fences.'
    )
    user = (
        "Clustered themes:\n"
        + json.dumps(theme_blocks(themes, tagged_reviews), ensure_ascii=False)
        + "\n\nQuestions (answer in this exact order):\n"
        + json.dumps(RESEARCH_QUESTIONS)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_stage3_response(raw_text, valid_review_ids):
    try:
        parsed = json.loads(strip_code_fence(raw_text))
    except json.JSONDecodeError as e:
        raise ValueError(f"Stage 3 response was not valid JSON: {e}") from e

    if not isinstance(parsed, list) or not parsed:
        raise ValueError("Stage 3 response JSON was not a non-empty list.")
    if len(parsed) != len(RESEARCH_QUESTIONS):
        raise ValueError(
            f"Expected {len(RESEARCH_QUESTIONS)} answers, got {len(parsed)}."
        )

    answers = []
    for item in parsed:
        if not isinstance(item, dict) or not REQUIRED_ANSWER_FIELDS.issubset(item.keys()):
            raise ValueError(f"Stage 3 response item missing required fields: {item!r}")

        supports = item["supporting_reviews"]
        if not isinstance(supports, list):
            raise ValueError(
                f"supporting_reviews was not a list for question {item.get('question')!r}"
            )
        if not (MIN_SUPPORTING <= len(supports) <= MAX_SUPPORTING):
            raise ValueError(
                f"Question {item.get('question')!r} has {len(supports)} supporting "
                f"reviews; expected {MIN_SUPPORTING}-{MAX_SUPPORTING}."
            )
        for support in supports:
            if not isinstance(support, dict) or not REQUIRED_SUPPORT_FIELDS.issubset(support.keys()):
                raise ValueError(f"Malformed supporting_review entry: {support!r}")
            if support["review_id"] not in valid_review_ids:
                raise ValueError(
                    f"Question {item.get('question')!r} cites unknown "
                    f"review_id {support['review_id']!r}"
                )
        answers.append(item)

    return answers


def synthesize(themes_result, tagged_reviews, rotator, run_date=None, http_post=None, max_retries=1):
    """Answers all 8 research questions for one timeframe. One LLM call
    (plus retries). Raises before ever calling the LLM if the in-window
    pool can't possibly satisfy the 3-5 supporting-review requirement."""
    run_date = run_date or datetime.now(timezone.utc)
    timeframe_days = themes_result["timeframe_days"]

    in_window = filter_by_timeframe(tagged_reviews, timeframe_days, run_date=run_date)
    valid_ids = {r["review_id"] for r in in_window}

    if len(valid_ids) < MIN_SUPPORTING:
        raise ValueError(
            f"Only {len(valid_ids)} tagged review(s) fall inside the "
            f"{timeframe_days}-day window — not enough to satisfy the "
            f"{MIN_SUPPORTING}-{MAX_SUPPORTING} supporting-review "
            "requirement per answer. Skipping the LLM call rather than "
            "risking fabricated evidence."
        )

    messages = build_stage3_messages(themes_result["themes"], in_window)

    answers = None
    last_error = None
    for _ in range(max_retries + 1):
        raw = chat_completion(rotator, messages, http_post=http_post)
        try:
            answers = parse_stage3_response(raw, valid_ids)
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

    if answers is None:
        raise ValueError(
            f"Stage 3 synthesis for the {timeframe_days}-day window failed to "
            f"produce valid JSON after {max_retries + 1} attempt(s): {last_error}"
        )

    return {
        "timeframe_days": timeframe_days,
        "pool_size": themes_result["pool_size"],
        "is_thin": themes_result["is_thin"],
        "answers": answers,
    }


def answers_path(run_date, timeframe_days):
    config.SYNTHESIS_DIR.mkdir(parents=True, exist_ok=True)
    return config.SYNTHESIS_DIR / f"answers_{run_date:%Y-%m-%d}_{timeframe_days}d.json"


def save_answers(result, run_date=None):
    run_date = run_date or datetime.now(timezone.utc)
    path = answers_path(run_date, result["timeframe_days"])
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return path


def load_answers(run_date, timeframe_days):
    path = answers_path(run_date, timeframe_days)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def get_or_compute_synthesis(themes_result, tagged_reviews, rotator, run_date=None, force=False, **kwargs):
    """Lazy, cached wrapper mirroring clustering.get_or_compute_themes:
    returns (result, was_cached)."""
    run_date = run_date or datetime.now(timezone.utc)
    timeframe_days = themes_result["timeframe_days"]

    if not force:
        cached = load_answers(run_date, timeframe_days)
        if cached is not None:
            return cached, True

    result = synthesize(themes_result, tagged_reviews, rotator, run_date=run_date, **kwargs)
    save_answers(result, run_date=run_date)
    return result, False


if __name__ == "__main__":
    from zepto_discovery.clustering import get_or_compute_themes
    from zepto_discovery.grok_client import KeyRotator, load_api_keys

    tagged_files = sorted(config.TAGGED_DIR.glob("reviews_tagged_*.json"))
    if not tagged_files:
        raise SystemExit("No tagged review file found in data/tagged/. Run zepto_discovery.tagging first.")
    tagged = json.loads(tagged_files[-1].read_text())

    rotator = KeyRotator(load_api_keys())
    for days in config.TIMEFRAME_OPTIONS_DAYS:
        themes_result, themes_cached = get_or_compute_themes(tagged, rotator, days)
        answers_result, answers_cached = get_or_compute_synthesis(themes_result, tagged, rotator)
        print(
            f"{days}-day window (themes {'cached' if themes_cached else 'computed'}, "
            f"synthesis {'cached' if answers_cached else 'computed'}): "
            f"pool={answers_result['pool_size']} thin={answers_result['is_thin']} "
            f"answers={len(answers_result['answers'])}"
        )
