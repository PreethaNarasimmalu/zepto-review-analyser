"""Stratified sampling of filtered reviews by star rating x recency chunk.

Sampling is proportional to each stratum's share of the eligible pool
(never oversampling negative reviews), capped at SAMPLE_SIZE_CAP, and
deterministic given a fixed seed so re-runs against the same filtered
data are reproducible for debugging.
"""

import json
import random
from datetime import datetime, timezone

from zepto_discovery import config
from zepto_discovery.util import parse_iso_datetime


def recency_chunk(review_date, run_date, window_days=None, chunks=None):
    """0 = most recent chunk, chunks - 1 = oldest."""
    window_days = config.TIME_WINDOW_DAYS if window_days is None else window_days
    chunks = config.RECENCY_CHUNKS if chunks is None else chunks
    age_days = (run_date - review_date).total_seconds() / 86400
    chunk_size = window_days / chunks
    idx = int(age_days // chunk_size)
    return min(max(idx, 0), chunks - 1)


def _stratum_key(review, run_date, window_days=None):
    rating = review.get("rating")
    chunk = recency_chunk(parse_iso_datetime(review["date"]), run_date, window_days=window_days)
    return (rating, chunk)


def _allocate(strata_counts, cap):
    """Largest-remainder allocation of `cap` across strata, proportional
    to each stratum's share of the total. Never allocates more than a
    stratum actually has."""
    total = sum(strata_counts.values())
    if total <= cap:
        return dict(strata_counts)

    exact = {k: cap * n / total for k, n in strata_counts.items()}
    alloc = {k: int(v) for k, v in exact.items()}
    remainder = cap - sum(alloc.values())

    order = sorted(strata_counts.keys(), key=lambda k: (-(exact[k] - alloc[k]), k))
    for k in order[:remainder]:
        alloc[k] += 1
    return alloc


def sample_reviews(reviews, cap=None, run_date=None, seed=None, window_days=None):
    """Returns (sampled_reviews, report).

    `window_days` should match whatever window the reviews were actually
    scraped over (defaults to config.TIME_WINDOW_DAYS) — it only affects
    how recency chunks are sized for stratification, so a smaller real
    scrape window doesn't silently get bucketed as if it were 90 days.

    report captures total eligible/sampled counts, whether the cap was
    reached, and a per-stratum breakdown of eligible vs. sampled counts.
    """
    cap = config.SAMPLE_SIZE_CAP if cap is None else cap
    run_date = run_date or datetime.now(timezone.utc)
    seed = config.RANDOM_SEED if seed is None else seed

    buckets = {}
    for review in reviews:
        key = _stratum_key(review, run_date, window_days=window_days)
        buckets.setdefault(key, []).append(review)
    for key in buckets:
        buckets[key].sort(key=lambda r: r.get("review_id") or "")

    strata_counts = {k: len(v) for k, v in buckets.items()}
    total = sum(strata_counts.values())
    used_full_pool = total <= cap
    alloc = _allocate(strata_counts, cap)

    rng = random.Random(seed)
    sampled = []
    strata_report = []
    for key in sorted(buckets.keys()):
        rating, chunk = key
        pool = buckets[key]
        n_alloc = alloc.get(key, 0)
        chosen = pool if n_alloc >= len(pool) else rng.sample(pool, n_alloc)
        sampled.extend(chosen)
        strata_report.append(
            {
                "rating": rating,
                "recency_chunk": chunk,
                "eligible_count": len(pool),
                "sampled_count": len(chosen),
            }
        )

    report = {
        "total_eligible": total,
        "total_sampled": len(sampled),
        "cap": cap,
        "used_full_pool": used_full_pool,
        "strata": strata_report,
    }
    return sampled, report


def save_sampled(sampled, report, run_date=None):
    run_date = run_date or datetime.now(timezone.utc)
    config.SAMPLED_DIR.mkdir(parents=True, exist_ok=True)
    sampled_path = config.SAMPLED_DIR / f"reviews_sampled_{run_date:%Y-%m-%d}.json"
    report_path = config.SAMPLED_DIR / f"sample_report_{run_date:%Y-%m-%d}.json"
    sampled_path.write_text(json.dumps(sampled, indent=2, ensure_ascii=False))
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return sampled_path, report_path


if __name__ == "__main__":
    filtered_files = sorted(config.FILTERED_DIR.glob("reviews_filtered_*.json"))
    if not filtered_files:
        raise SystemExit("No filtered review file found in data/filtered/. Run zepto_discovery.filters first.")
    latest = filtered_files[-1]
    filtered_reviews = json.loads(latest.read_text())
    sampled_reviews, sample_report = sample_reviews(filtered_reviews)
    sampled_path, report_path = save_sampled(sampled_reviews, sample_report)
    print(f"Sampled {sample_report['total_sampled']}/{sample_report['total_eligible']} reviews -> {sampled_path}")
    print(f"Used full pool: {sample_report['used_full_pool']}")
    print(f"Strata report -> {report_path}")
