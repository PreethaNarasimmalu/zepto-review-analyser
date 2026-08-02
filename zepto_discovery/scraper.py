"""Scrapes Play Store reviews for the target app back to a fixed day window."""

import json
from datetime import datetime, timedelta, timezone

from google_play_scraper import Sort, reviews

from zepto_discovery import config


def scrape_reviews(
    run_date=None,
    app_id=None,
    locale=None,
    window_days=None,
    page_size=200,
    fetch_page=None,
):
    """Paginate Play Store reviews newest-first, stopping once past window_days.

    `fetch_page` is an injectable seam for testing: a callable
    (app_id, lang, country, sort, count, continuation_token) -> (reviews, next_token).
    Defaults to google_play_scraper.reviews.
    """
    run_date = run_date or datetime.now(timezone.utc)
    app_id = app_id or config.PLAY_STORE_APP_ID
    locale = locale or config.PLAY_STORE_LOCALE
    window_days = window_days if window_days is not None else config.TIME_WINDOW_DAYS
    fetch_page = fetch_page or _fetch_page

    cutoff = run_date - timedelta(days=window_days)
    lang, country = _split_locale(locale)

    collected = []
    token = None
    is_first_page = True
    while True:
        batch, token = fetch_page(
            app_id,
            lang=lang,
            country=country,
            sort=Sort.NEWEST,
            count=page_size,
            continuation_token=token,
        )
        if not batch:
            if is_first_page:
                # google_play_scraper swallows network/parsing errors internally
                # and just returns an empty list — an empty *first* page is a much
                # stronger signal of a silent failure than of a genuinely
                # review-free app, so treat it as an error rather than a result.
                raise RuntimeError(
                    "Scraper returned zero reviews on the first page for "
                    f"{app_id!r}. This usually means the underlying request "
                    "failed silently (network/proxy block, rate limit, or a "
                    "Play Store response the parser didn't recognize) rather "
                    "than the app genuinely having no reviews — check "
                    "connectivity before trusting an empty result."
                )
            break
        is_first_page = False

        in_window = [r for r in batch if _as_utc(r["at"]) >= cutoff]
        collected.extend(in_window)

        crossed_boundary = len(in_window) < len(batch)
        if crossed_boundary or token is None:
            break

    return [_normalize(r) for r in collected]


def save_raw(records, run_date=None):
    run_date = run_date or datetime.now(timezone.utc)
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = config.RAW_DIR / f"reviews_raw_{run_date:%Y-%m-%d}.json"
    path.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    return path


def _fetch_page(app_id, lang, country, sort, count, continuation_token):
    return reviews(
        app_id,
        lang=lang,
        country=country,
        sort=sort,
        count=count,
        continuation_token=continuation_token,
    )


def _split_locale(locale):
    lang, _, country = locale.partition("_")
    return lang, country.lower()


def _as_utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _normalize(review):
    return {
        "review_id": review.get("reviewId"),
        "text": review.get("content"),
        "rating": review.get("score"),
        "date": _as_utc(review["at"]).isoformat(),
        "thumbs_up": review.get("thumbsUpCount"),
    }


if __name__ == "__main__":
    records = scrape_reviews()
    path = save_raw(records)
    print(f"Scraped {len(records)} reviews within {config.TIME_WINDOW_DAYS} days -> {path}")
