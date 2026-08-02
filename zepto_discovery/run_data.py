"""Pure-Python data-loading helpers for the Streamlit UI (Phase 8).

Kept separate from app/main.py so the "what data does the app show"
logic is unit-testable without a Streamlit runtime.
"""

import json
from datetime import datetime, timezone

from zepto_discovery import config
from zepto_discovery.clustering import get_or_compute_themes, load_themes
from zepto_discovery.synthesis import get_or_compute_synthesis, load_answers


def _latest_date_from_filenames(directory, prefix):
    """Finds the most recent run_date among files named
    '<prefix>_<YYYY-MM-DD>...' in `directory`. Returns None if there are none."""
    if not directory.exists():
        return None
    dates = []
    for path in directory.glob(f"{prefix}_*"):
        stem = path.name[len(prefix) + 1 :]
        date_str = stem[:10]  # YYYY-MM-DD
        try:
            dates.append(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc))
        except ValueError:
            continue
    return max(dates) if dates else None


def latest_run_date():
    """The most recent run_date for which Stage 1 tagging has completed —
    a run isn't meaningful to show in the UI until at least tagging exists."""
    return _latest_date_from_filenames(config.TAGGED_DIR, "reviews_tagged")


def _count_records(path):
    if not path.exists():
        return None
    return len(json.loads(path.read_text()))


def funnel_counts(run_date):
    """Review counts at each pipeline stage for run_date, or None for a
    stage whose file doesn't exist (e.g. a partial/failed run)."""
    date_str = f"{run_date:%Y-%m-%d}"
    return {
        "scraped": _count_records(config.RAW_DIR / f"reviews_raw_{date_str}.json"),
        "filtered": _count_records(config.FILTERED_DIR / f"reviews_filtered_{date_str}.json"),
        "sampled": _count_records(config.SAMPLED_DIR / f"reviews_sampled_{date_str}.json"),
        "tagged": _count_records(config.TAGGED_DIR / f"reviews_tagged_{date_str}.json"),
    }


def load_tagged_reviews(run_date):
    path = config.TAGGED_DIR / f"reviews_tagged_{run_date:%Y-%m-%d}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def load_timeframe_view(run_date, timeframe_days):
    """Returns {'themes': ..., 'answers': ...}, either value None if that
    timeframe hasn't been computed yet for this run_date."""
    return {
        "themes": load_themes(run_date, timeframe_days),
        "answers": load_answers(run_date, timeframe_days),
    }


def ensure_timeframe_computed(run_date, timeframe_days, tagged_reviews, rotator, http_post=None):
    """Loads a cached (themes, answers) pair for this timeframe, computing
    and caching whichever half is missing. Returns
    (themes_result, answers_result, was_newly_computed)."""
    themes_result, themes_cached = get_or_compute_themes(
        tagged_reviews, rotator, timeframe_days, run_date=run_date, http_post=http_post
    )
    answers_result, answers_cached = get_or_compute_synthesis(
        themes_result, tagged_reviews, rotator, run_date=run_date, http_post=http_post
    )
    return themes_result, answers_result, not (themes_cached and answers_cached)
