"""End-to-end integration: chains real Phase 1-6 functions together
(not hand-rolled shortcuts) to prove each phase's output schema is
actually consumable by the next phase, not just individually correct.
"""

import json as _json
from datetime import datetime, timedelta, timezone

from zepto_discovery import config
from zepto_discovery.clustering import cluster_themes, filter_by_timeframe
from zepto_discovery.filters import filter_reviews
from zepto_discovery.grok_client import KeyRotator
from zepto_discovery.sampler import sample_reviews
from zepto_discovery.scraper import _normalize
from zepto_discovery.synthesis import RESEARCH_QUESTIONS, synthesize
from zepto_discovery.tagging import tag_reviews

RUN_DATE = datetime(2026, 8, 2, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


def llm_response(content):
    return FakeResponse(200, {"choices": [{"message": {"content": content}}]})


def raw_scraper_shaped_review(review_id, text, rating, days_old):
    """Mimics exactly what scraper._normalize() produces from a raw
    google_play_scraper record, so this test starts from the real
    Phase 1 output shape rather than a hand-rolled one."""
    return _normalize(
        {
            "reviewId": review_id,
            "content": text,
            "score": rating,
            "at": RUN_DATE - timedelta(days=days_old),
            "thumbsUpCount": 0,
        }
    )


CONTENT_BY_RATING = {
    5: [
        "Fast delivery every single time, love ordering snacks here",
        "Great discounts on fruits this week, very happy with quality",
        "Milk and bread arrive fresh every morning, my go-to app now",
    ],
    4: [
        "Good app overall but delivery slot booking could be smoother",
        "Nice range of vegetables, wish they had more organic options",
    ],
    3: [
        "Prices are a bit higher than the local kirana store nearby",
        "App works fine but customer support response is slow",
    ],
    2: [
        "Vegetables arrived rotten twice this month, quite disappointed",
        "Delivery boy was rude and packaging was damaged on arrival",
    ],
    1: [
        "App crashes every time I try to checkout with more than 10 items",
        "10 minute delivery promise is a scam, took over an hour today",
        "Customer support never refunded my money for the damaged order",
    ],
}

NOISE_REVIEWS = ["nice app", "5 stars", "😂😂😂", "ok", "worst app"]


def build_synthetic_scrape(days_old_choices=(1, 20, 40, 60, 85)):
    reviews = []
    i = 0
    for rating, texts in CONTENT_BY_RATING.items():
        for text in texts:
            for days_old in days_old_choices:
                reviews.append(raw_scraper_shaped_review(f"r{i}", text, rating, days_old))
                i += 1
    for text in NOISE_REVIEWS:
        reviews.append(raw_scraper_shaped_review(f"noise{i}", text, 5, 2))
        i += 1
    return reviews


def fake_stage1_http_post(url, headers, json, timeout):
    """Deterministic keyword-based stand-in for Grok's Stage 1 tagging."""
    content = json["messages"][-1]["content"]
    ids_in_batch = [
        line.split("id=")[1].split(" ")[0]
        for line in content.splitlines()
        if line.startswith("- id=")
    ]
    text_by_id = {
        rid: line.split('text="', 1)[1].rsplit('"', 1)[0]
        for rid, line in zip(
            ids_in_batch,
            (line for line in content.splitlines() if line.startswith("- id=")),
        )
    }
    tags = []
    for rid in ids_in_batch:
        lower = text_by_id[rid].lower()
        if "vegetable" in lower or "fruit" in lower:
            category = "produce"
        elif "milk" in lower or "bread" in lower:
            category = "dairy/bakery"
        else:
            category = None
        sentiment = "positive" if any(w in lower for w in ("fast", "great", "love", "fresh", "nice")) else (
            "negative" if any(w in lower for w in ("crash", "scam", "rotten", "rude", "never refunded")) else "neutral"
        )
        theme_tags = [w for w in ("delivery", "quality", "support", "price", "crash") if w in lower] or ["general"]
        tags.append({"review_id": rid, "category": category, "sentiment": sentiment, "theme_tags": theme_tags})
    return llm_response(_json.dumps(tags))


def fake_stage2_http_post(url, headers, json, timeout):
    """Deterministic stand-in for Grok's Stage 2 clustering: groups by
    whichever raw tag appears first for each tag entry it's given."""
    content = json["messages"][-1]["content"]
    tag_list = _json.loads(content.split("Raw tag frequency list:\n", 1)[1])
    themes = [
        {
            "name": f"theme: {entry['tag']}",
            "description": f"Reviews mentioning {entry['tag']}",
            "count": entry["count"],
            "example_review_ids": entry["example_review_ids"],
        }
        for entry in tag_list
    ]
    # Pad/trim to a plausible 8-12 range for realism, without breaking validity.
    while len(themes) < 8:
        themes.append(dict(themes[-1]))
    return llm_response(_json.dumps(themes))


def fake_stage3_http_post(url, headers, json, timeout):
    """Deterministic stand-in for Grok's Stage 3 synthesis: cites real
    review_id/excerpt/theme triples straight out of the theme data it
    was given, rotating which ones back each question for variety."""
    content = json["messages"][-1]["content"]
    themes_json = content.split("Clustered themes:\n", 1)[1].split("\n\nQuestions", 1)[0]
    themes = _json.loads(themes_json)

    pool = []
    seen = set()
    for theme in themes:
        for ex in theme["example_reviews"]:
            if ex["review_id"] not in seen:
                seen.add(ex["review_id"])
                pool.append({"review_id": ex["review_id"], "excerpt": ex["excerpt"], "theme": theme["name"]})

    k = min(3, len(pool))
    answers = [
        {
            "question": question,
            "answer": f"Synthesized answer for: {question}",
            "supporting_reviews": [pool[(i + j) % len(pool)] for j in range(k)],
        }
        for i, question in enumerate(RESEARCH_QUESTIONS)
    ]
    return llm_response(_json.dumps(answers))


def test_full_pipeline_phase1_through_phase6_integration():
    raw = build_synthetic_scrape()

    filtered, drop_log = filter_reviews(raw)
    assert len(filtered) < len(raw)  # noise reviews actually got dropped
    assert set(drop_log.values()) <= {"too_short", "emoji_only", "generic", "rating_restatement"}

    sampled, sample_report = sample_reviews(filtered, cap=1200, run_date=RUN_DATE)
    assert sample_report["used_full_pool"] is True  # pool is well under the cap
    assert len(sampled) == len(filtered)

    rotator = KeyRotator(["fake-key"])
    tagged = tag_reviews(sampled, rotator, batch_size=6, http_post=fake_stage1_http_post)
    assert len(tagged) == len(sampled)
    # Phase 4's output must carry forward Phase 1's original date/rating/text untouched.
    original_by_id = {r["review_id"]: r for r in sampled}
    for t in tagged:
        assert t["date"] == original_by_id[t["review_id"]]["date"]
        assert t["rating"] == original_by_id[t["review_id"]]["rating"]
        assert t["text"] == original_by_id[t["review_id"]]["text"]

    # Phase 5 consumes Phase 4's exact output schema directly.
    result_90 = cluster_themes(tagged, rotator, timeframe_days=90, run_date=RUN_DATE, http_post=fake_stage2_http_post)
    assert result_90["pool_size"] == len(filter_by_timeframe(tagged, 90, run_date=RUN_DATE))
    assert len(result_90["themes"]) >= 8

    # Every example_review_id Stage 2 cites must trace back to a real,
    # in-window tagged review — the validation requirement ARCHITECTURE.md
    # asks for, checked here across the whole real chain, not in isolation.
    tagged_ids_in_window = {r["review_id"] for r in filter_by_timeframe(tagged, 90, run_date=RUN_DATE)}
    for theme in result_90["themes"]:
        for rid in theme["example_review_ids"]:
            assert rid in tagged_ids_in_window

    # A narrower timeframe must produce a smaller (or equal) pool, proving
    # the date field survived Phase 1 -> Phase 4 -> Phase 5 unchanged and
    # is actually usable for filtering.
    result_7 = cluster_themes(tagged, rotator, timeframe_days=7, run_date=RUN_DATE, http_post=fake_stage2_http_post)
    assert result_7["pool_size"] <= result_90["pool_size"]
    assert result_7["is_thin"] is True  # realistic small synthetic pool should trip the guard

    # Phase 6 consumes Phase 5's exact output schema (themes + example_review_ids)
    # directly, plus Phase 4's tagged reviews for excerpt lookup.
    synthesis_90 = synthesize(result_90, tagged, rotator, run_date=RUN_DATE, http_post=fake_stage3_http_post)
    assert [a["question"] for a in synthesis_90["answers"]] == RESEARCH_QUESTIONS
    for answer in synthesis_90["answers"]:
        assert 3 <= len(answer["supporting_reviews"]) <= 5
        for support in answer["supporting_reviews"]:
            assert support["review_id"] in tagged_ids_in_window

    # The thin 7-day window must still produce a fully schema-valid
    # synthesis (3-5 real supporting reviews per answer), not degraded
    # or fabricated evidence, per ARCHITECTURE.md's thin-window
    # requirement carrying through from Stage 2 into Stage 3.
    synthesis_7 = synthesize(result_7, tagged, rotator, run_date=RUN_DATE, http_post=fake_stage3_http_post)
    tagged_ids_in_7day_window = {r["review_id"] for r in filter_by_timeframe(tagged, 7, run_date=RUN_DATE)}
    assert len(synthesis_7["answers"]) == 8
    assert synthesis_7["is_thin"] is True
    for answer in synthesis_7["answers"]:
        assert 3 <= len(answer["supporting_reviews"]) <= 5
        for support in answer["supporting_reviews"]:
            assert support["review_id"] in tagged_ids_in_7day_window
