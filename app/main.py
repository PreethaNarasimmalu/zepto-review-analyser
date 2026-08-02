"""Streamlit UI (Phase 8) — shows the last completed pipeline run, lets
the user switch between 7/30/90-day timeframes, and can trigger a fresh
pipeline run. Data-loading logic lives in zepto_discovery.run_data so it
stays unit-testable without a Streamlit runtime.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zepto_discovery import config
from zepto_discovery.filters import filter_reviews, save_filtered
from zepto_discovery.grok_client import KeyRotator, load_api_keys
from zepto_discovery.run_data import (
    ensure_timeframe_computed,
    funnel_counts,
    latest_run_date,
    load_tagged_reviews,
    load_timeframe_view,
)
from zepto_discovery.sampler import sample_reviews, save_sampled
from zepto_discovery.scraper import save_raw, scrape_reviews
from zepto_discovery.tagging import save_tagged, tag_reviews

TIMEFRAME_LABELS = {7: "Last 7 days", 30: "Last 30 days", 90: "Last 90 days"}

st.set_page_config(page_title="Zepto Category-Discovery Engine", page_icon="\U0001F5C2", layout="wide")


def run_full_pipeline():
    """Phases 1-4: scrape, filter, sample, tag. Run explicitly via the
    sidebar button — the UI never re-runs this automatically."""
    run_date = datetime.now(timezone.utc)

    with st.spinner("Scraping Play Store reviews..."):
        raw = scrape_reviews(run_date=run_date)
        save_raw(raw, run_date=run_date)

    with st.spinner("Filtering low-signal reviews..."):
        filtered, drop_log = filter_reviews(raw)
        save_filtered(filtered, drop_log, run_date=run_date)

    with st.spinner("Sampling..."):
        sampled, sample_report = sample_reviews(filtered, run_date=run_date)
        save_sampled(sampled, sample_report, run_date=run_date)

    with st.spinner("Tagging with Grok (Stage 1)..."):
        rotator = KeyRotator(load_api_keys())
        tagged = tag_reviews(sampled, rotator)
        save_tagged(tagged, run_date=run_date)

    return run_date


def render_sidebar(run_date):
    st.sidebar.header("Pipeline")
    if run_date is not None:
        st.sidebar.caption(f"Showing run from **{run_date:%Y-%m-%d}**")
    else:
        st.sidebar.caption("No completed run yet.")

    if st.sidebar.button("Run new pipeline", help="Scrapes, filters, samples, and tags the last 90 days of reviews."):
        try:
            new_run_date = run_full_pipeline()
            st.sidebar.success(f"Run complete for {new_run_date:%Y-%m-%d}.")
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Pipeline run failed: {e}")


def render_funnel(run_date):
    counts = funnel_counts(run_date)
    cols = st.columns(4)
    cols[0].metric("Scraped", counts["scraped"] if counts["scraped"] is not None else "—")
    cols[1].metric("Filtered", counts["filtered"] if counts["filtered"] is not None else "—")
    cols[2].metric("Sampled", counts["sampled"] if counts["sampled"] is not None else "—")
    cols[3].metric("Tagged", counts["tagged"] if counts["tagged"] is not None else "—")


def render_timeframe_view(run_date, timeframe_days):
    view = load_timeframe_view(run_date, timeframe_days)

    if view["themes"] is None or view["answers"] is None:
        st.info(f"The {TIMEFRAME_LABELS[timeframe_days].lower()} view hasn't been analyzed yet.")
        if st.button(f"Analyze {TIMEFRAME_LABELS[timeframe_days].lower()}", key=f"analyze_{timeframe_days}"):
            tagged = load_tagged_reviews(run_date)
            try:
                with st.spinner("Clustering and synthesizing (one-time Grok calls for this window)..."):
                    rotator = KeyRotator(load_api_keys())
                    ensure_timeframe_computed(run_date, timeframe_days, tagged, rotator)
                st.rerun()
            except Exception as e:
                st.error(f"Couldn't analyze this timeframe: {e}")
        return

    themes_result = view["themes"]
    answers_result = view["answers"]

    st.subheader(f"{answers_result['pool_size']} reviews in the {TIMEFRAME_LABELS[timeframe_days].lower()}")
    if answers_result["is_thin"]:
        st.warning(
            f"Only {answers_result['pool_size']} reviews fall in this window "
            f"(below the {config.THIN_WINDOW_MIN_REVIEWS}-review threshold) — "
            "answers below may be less robust than the fuller windows."
        )

    st.header("Research questions")
    for answer in answers_result["answers"]:
        with st.expander(answer["question"]):
            st.write(answer["answer"])
            st.caption("Supporting reviews")
            for support in answer["supporting_reviews"]:
                st.markdown(f"- **[{support['theme']}]** _{support['excerpt']}_ — `{support['review_id']}`")

    st.header("Themes")
    for theme in sorted(themes_result["themes"], key=lambda t: -t["count"]):
        with st.expander(f"{theme['name']} — {theme['count']} mentions"):
            st.write(theme["description"])
            st.caption("Example reviews")
            for review_id in theme["example_review_ids"]:
                st.markdown(f"- `{review_id}`")

    st.header("Ask a question")
    st.text_input(
        "Ask an ad-hoc question about these reviews",
        disabled=True,
        help="Coming in Phase 10 (re-query) — not yet built.",
    )


def main():
    st.title("Zepto Category-Discovery Engine")
    st.caption(
        "Why don't Zepto shoppers explore new categories? Answers synthesized "
        "from Play Store reviews, every claim traceable to source text."
    )

    run_date = latest_run_date()
    render_sidebar(run_date)

    if run_date is None:
        st.info(
            "No completed pipeline run found yet. Click **Run new pipeline** "
            "in the sidebar to scrape, filter, sample, and tag the last 90 "
            "days of reviews."
        )
        return

    render_funnel(run_date)

    label = st.radio("Timeframe", list(TIMEFRAME_LABELS.values()), index=2, horizontal=True)
    timeframe_days = next(days for days, lbl in TIMEFRAME_LABELS.items() if lbl == label)
    render_timeframe_view(run_date, timeframe_days)


main()
