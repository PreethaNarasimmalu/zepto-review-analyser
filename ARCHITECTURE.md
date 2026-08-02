# Zepto Category-Discovery Engine — Architecture Document

## 1. Purpose & scope

An AI-powered discovery engine that analyzes Zepto's Play Store reviews to
understand why users don't explore new product categories. Output feeds a
problem statement, user interview guide, and MVP direction for a fellowship
graduation project.

- **Data source:** Play Store reviews only, app `com.zeptoconsumerapp`
  ([listing](https://play.google.com/store/apps/details?id=com.zeptoconsumerapp&hl=en_IN)).
  No App Store, Reddit, X, or community page (none exists for Zepto).
- **Time window:** last 90 days from run date.
- **LLM provider:** Grok API, 25–30 keys provided as Streamlit secrets, with
  rotation and automatic failover.
- **Target LLM budget:** 5–10 calls for a full pipeline run.
- **Hosting:** Streamlit app (final deployed artifact).

This document defines phases only. **No implementation code is written until
each phase is explicitly approved.** This document itself is pushed to git
before any code exists.

---

## 2. Pipeline overview

```
Phase 0: Repo & project scaffolding
Phase 1: Scraping
Phase 2: Filtering (pure Python, zero LLM calls)
Phase 3: Sampling (stratified, pure Python)
Phase 4: Stage 1 — Tagging (batched LLM calls)
Phase 5: Stage 2 — Clustering (1 LLM call)
Phase 6: Stage 3 — Synthesis (1 LLM call)
Phase 7: Key rotation & failover layer (Grok client wrapper)
Phase 8: Streamlit UI
Phase 9: Deployment
Phase 10: Re-query / ad-hoc question capability
```

Phase 7 (key rotation) is a cross-cutting concern used by Phases 4–6 and 10,
but is called out as its own phase because it needs independent testing
(simulated rate-limit/error responses) before being trusted inside the
pipeline. It will be built alongside Phase 4, before the first real Stage 1
call is made against the Grok API.

Each phase below lists: what gets built, inputs/outputs, key decisions to
confirm, and how it will be tested. Phases are built and pushed **one at a
time**, only after explicit go-ahead, in the order listed above.

---

## Phase 0 — Repo & project scaffolding

**What's built:**
- Directory structure: `src/`, `data/` (gitignored raw/intermediate data),
  `tests/`, `app/` (Streamlit).
- `requirements.txt`, `.gitignore` (exclude scraped data, API keys, `.env`).
- `status.md` initialized with project overview and "Phase 0 complete."
- Config module stub defining constants (app ID, time window, sample size
  cap) in one place so later phases don't hardcode them.

**Inputs:** This architecture doc.
**Outputs:** Empty-but-runnable project skeleton, no business logic.

**Testing:** `python -m pytest` runs (even with zero tests) without error;
directory structure reviewed manually.

---

## Phase 1 — Scraping

**What's built:**
- A scraper module pulling Play Store reviews for `com.zeptoconsumerapp`
  (likely via the `google-play-scraper` Python library, which supports
  fetching by date range/sort-by-newest and pagination — no API key
  needed since it scrapes the public Play Store page).
- Logic to paginate until reviews fall outside the 90-day window from run
  date, then stop.
- Raw output stored as a local JSON/CSV artifact (not committed — reviews
  are user data and volume can be large): review ID, text, rating (1–5),
  date, author (or hashed/dropped for privacy), thumbs-up count.

**Inputs:** App ID, run date (defaults to today).
**Outputs:** `data/raw/reviews_<run_date>.json` — one record per review.

**Key decisions to confirm during build:**
- Exact scraping library/method (subject to what's actually installable
  and reliable in this environment — will confirm during Phase 1 build,
  not assumed here).
- Whether Play Store review sort order lets us stop early once we cross
  the 90-day boundary, or whether we must pull everything and filter by
  date after the fact.

**Testing:**
- Unit test: date-window boundary logic with mocked review timestamps
  (reviews exactly at day 90, day 91 boundary).
- Real run: scrape actual Zepto reviews, manually inspect a sample of
  ~20 for correct fields (rating, date, text intact, no truncation/encoding
  issues, especially for non-English/Hinglish text which is common in
  Indian app reviews).
- Record actual review count pulled in `status.md`.

---

## Phase 2 — Filtering (pre-LLM, plain Python, zero LLM calls)

**What's built:**
- Pure-Python filter chain applied to raw scraped reviews:
  1. Drop reviews under ~3 words.
  2. Drop emoji-only reviews (strip emoji/symbols, check if any word
     characters remain).
  3. Drop generic/meaningless reviews — combine word-count heuristics with
     a check for specific noun/content words beyond generic adjectives
     ("nice", "good", "worst", "bad", "amazing", "app"). Approach: a
     stoplist of generic-adjective-only patterns + a check that at least
     one content word outside a small generic vocabulary is present.
  4. Drop star-rating restatements ("5 stars", "1 star worst") via regex
     matching numeral/word + "star(s)" patterns where that's ~the entire
     review.
- Each filter is a separate, independently testable function so the chain
  is auditable (which rule dropped which review) — output includes a
  drop-reason log for transparency, not just a final filtered list.

**Inputs:** `data/raw/reviews_<run_date>.json`.
**Outputs:** `data/filtered/reviews_filtered_<run_date>.json` +
`data/filtered/drop_log_<run_date>.json` (review ID → which rule dropped it).

**Testing:**
- Unit tests per filter rule with hand-written example reviews (including
  edge cases: a 3-word review that IS substantive vs. one that isn't;
  emoji mixed with real text vs. emoji-only; "worst app 1 star" as a
  compound generic+rating case).
- Real run: apply to Phase 1's actual scraped data, manually spot-check
  ~30 dropped and ~30 kept reviews to confirm the heuristics aren't
  over- or under-filtering. Report before/after counts in `status.md`.

---

## Phase 3 — Sampling

**What's built:**
- Stratification by star rating (1–5) × recency (3 chunks across the
  90-day window = up to 15 strata).
- Proportional sampling: each stratum's sample size is proportional to its
  share of the *eligible filtered pool*, not artificially balanced —
  preserves natural sentiment distribution so habitual/positive behavior
  is represented, not just complaints.
- Hard cap at ~1,200 total. If eligible pool < 1,200, take everything and
  flag this explicitly in `status.md` (with the actual pool size).
- Deterministic seeding (fixed random seed) so a re-run against the same
  filtered data is reproducible for debugging.

**Inputs:** `data/filtered/reviews_filtered_<run_date>.json`.
**Outputs:** `data/sampled/reviews_sampled_<run_date>.json` (≤1,200
records) + a small stratification report (counts per rating × recency
bucket, before and after sampling).

**Testing:**
- Unit test: given a synthetic filtered pool with known strata
  distribution, verify sampled output preserves proportions within
  rounding tolerance and respects the 1,200 cap.
- Unit test: pool-smaller-than-cap case returns everything and sets the
  "used full pool" flag.
- Real run: sample from Phase 2's actual filtered output, review the
  stratification report by eye for sanity (e.g., are 1-star and 5-star
  reviews both present in reasonable proportion; are all 3 recency
  chunks represented).

---

## Phase 4 — Stage 1: Tagging (batched LLM calls)

**What's built:**
- Batching logic: split the ≤1,200 sampled reviews into batches of
  ~150–175 reviews each (~6–7 calls total) — **exact batch size to be
  confirmed empirically during this phase** by testing Grok's JSON
  output reliability at that size (this is the explicit open item from
  the spec).
- Prompt template: per review, extract category mentioned, sentiment,
  and raw theme tags (short phrases close to the reviewer's own
  language). Output: strict JSON array, one object per review, carrying
  the review ID through so results map back to source text.
- JSON parsing with validation (schema check per response) and a retry
  path if a batch returns malformed JSON (retry once with a stricter
  "return valid JSON only" reminder before failing that batch loudly).
- Persist output with full metadata per review (review ID, date, star
  rating, category, sentiment, theme tags) in a structured store
  (JSON/Parquet) — this is the data Phase 10's re-query capability, and
  the Phase 8 timeframe selector, both read from later, so schema needs
  to be stable and self-describing.
- Written both to local disk (for the run in progress) and to the
  external persistence store (see §3 Storage & persistence) so it
  survives a Streamlit Community Cloud restart — this is the single
  90-day tagged pool everything downstream filters from.
- Uses the key-rotation/failover client from Phase 7.

**Inputs:** `data/sampled/reviews_sampled_<run_date>.json`, Grok API keys
via `st.secrets`.
**Outputs:** `data/tagged/reviews_tagged_<run_date>.json` locally, plus
the same records upserted to the external store, keyed by run date —
one record per review with original metadata + Stage 1 tags.

**Testing:**
- Dry run against a small hand-picked batch (~20 reviews) to validate
  prompt output shape before committing to a batch size.
- Empirical test at 150, 175 (and if needed a lower fallback like 100)
  reviews/batch to measure JSON validity rate; pick the largest size
  that stays reliable, document the choice + evidence in `status.md`.
- Unit tests: JSON parsing/validation function against both well-formed
  and deliberately malformed sample responses.
- Real run: full ~1,200-review tagging run, spot-check ~20 tagged
  records against original review text for tag accuracy.
- Confirm total call count lands in the 6–7 range as budgeted.

---

## Phase 5 — Stage 2: Clustering (1 LLM call per timeframe, cached)

**What's built:**
- Aggregate Stage 1's raw tags (not raw review text) into a frequency-
  ranked tag list to keep the prompt compact enough for a single call.
- **Parameterized by timeframe.** The scrape/sample/tag steps (Phases
  1–4) run once, at the full 90-day window. Clustering itself is run
  separately per timeframe the Phase 8 UI exposes — 7 days / 30 days /
  90 days — by filtering the tagged pool down to reviews whose stored
  date falls inside that window *before* aggregating tags. This is not
  a re-sample: it's a date filter over the one tagged pool.
- One LLM call per timeframe: group that window's tags into 8–12
  higher-level themes, bottom-up (themes emerge from the tag data itself
  — the 8 research questions are **not** given to the model as pre-set
  buckets, to avoid confirmation bias, per spec).
- Output per theme: theme name/description, frequency count, and linked
  example review IDs (so Stage 3 and the validation requirement can trace
  back to source).
- Computed lazily: only runs the first time a given timeframe is
  requested (default 90 days runs as part of the main pipeline; 7-day
  and 30-day only run if/when someone picks them in the UI), then the
  result is cached (external store) so re-selecting the same timeframe
  later costs zero LLM calls.
- **Thin-window guard:** if the filtered pool for a timeframe falls below
  a minimum size (e.g. ~50 reviews), record that alongside the theme
  output so Phase 8 can surface a "small sample, interpret with caution"
  notice rather than silently presenting it as equally robust.

**Inputs:** tagged pool (external store or local), a timeframe (7/30/90
days).
**Outputs:** `themes_<run_date>_<timeframe>.json` — 8–12 themes with
counts, example review ID lists, and the filtered pool size.

**Testing:**
- Unit test: date-filter function against a synthetic tagged pool,
  confirm correct reviews included/excluded at each window boundary.
- Real run against actual Stage 1 output for all three timeframes (no
  meaningful way to unit-test LLM clustering quality; validation is
  manual).
- Manual review: for each theme, pull 3–5 linked example reviews and
  confirm they actually belong to that theme (this is a dry run of the
  Phase-6 validation requirement, done one stage early to catch
  clustering problems before synthesis compounds them).
- Sanity check theme count is within 8–12 and no theme is a near-empty
  outlier or a catch-all "other" bucket that swallows too much signal,
  for each of the three timeframes.
- Confirm the 7-day window actually triggers the thin-window guard on
  realistic data, and that re-selecting a cached timeframe makes no new
  LLM call.

---

## Phase 6 — Stage 3: Synthesis (1 LLM call per timeframe, cached)

**What's built:**
- Same timeframe parameterization as Phase 5: one call per timeframe
  (7/30/90 days), computed lazily on first request and cached — the
  90-day answer is produced as part of the main run; 7-day/30-day only
  get computed if a user actually selects them in the UI.
- One LLM call fed that timeframe's clustered themes + counts + example
  reviews, prompted to answer the 8 fixed research questions:
  1. Why do users repeatedly buy from the same categories?
  2. What prevents users from exploring new categories?
  3. How do users discover products today?
  4. What role do habits play in shopping behavior?
  5. What information do users need before trying a new category?
  6. What frustrations emerge repeatedly?
  7. Which user segments are more likely to experiment?
  8. What unmet needs emerge consistently across discussions?
- **Validation requirement built into the output schema itself:** every
  answer must carry 3–5 linked source review IDs (+ short excerpt) it
  draws from, so this is enforced by the prompt/schema, not left as an
  assertion. Output structure: `{question, answer, supporting_reviews:
  [{review_id, excerpt, theme}]}`.
- Post-call validation step (pure Python): confirm every answer object
  actually has 3–5 supporting reviews and that those review IDs exist in
  the tagged dataset — reject/flag the response if not, rather than
  silently shipping an unsupported claim.

**Inputs:** `themes_<run_date>_<timeframe>.json`, tagged reviews (for
excerpt lookup).
**Outputs:** `answers_<run_date>_<timeframe>.json`.

**Testing:**
- Unit test: the post-call validator against a hand-crafted malformed
  response (missing supporting reviews, invalid review ID) — confirm it
  flags correctly.
- Real run: full synthesis call for all three timeframes, manual
  read-through of all 8 answers per timeframe against their linked
  source reviews to confirm the evidence actually supports the claim
  (this is the spec's explicit spot-check requirement).
- Confirm the thin-window guard from Phase 5 carries through: a
  low-evidence timeframe's answers are still schema-valid (3–5 sources
  where available) rather than fabricating filler support.

---

## Phase 7 — Key rotation & failover layer

**What's built:**
- A Grok API client wrapper used by Phases 4–6 and 10:
  - Loads all keys from `st.secrets` (never hardcoded/`.env`).
  - Rotates across keys (e.g., round-robin) for load distribution.
  - On rate-limit (429) or error response, marks that key temporarily
    unusable and fails over to the next key automatically, retrying the
    same request.
  - If all keys are exhausted/failing, raises a clear error rather than
    hanging or silently returning partial data.
- Basic in-memory tracking of per-key state (cooldown timestamp) for the
  duration of a run.

**Inputs:** `st.secrets["GROK_API_KEYS"]` (list) or equivalent secret
structure — exact key naming convention confirmed during this phase.
**Outputs:** A reusable client module imported by Stage 1/2/3 and Phase 10.

**Testing:**
- Unit tests with a mocked HTTP layer: simulate a 429 on key 1, confirm
  fallback to key 2; simulate all keys failing, confirm the clear error
  path; simulate a key recovering after cooldown.
- Integration check: run one real, cheap Grok call through the wrapper
  with real keys to confirm end-to-end wiring before Phase 4 relies on it
  for a full run.

---

## Phase 8 — Streamlit UI

**What's built:**
- Streamlit app (`app/main.py`) presenting:
  - Pipeline run controls (trigger a run, or load a previous run's
    saved outputs — reruns are expensive, so the UI defaults to showing
    the last completed run rather than re-running by default).
  - **Timeframe selector** — last 7 / 30 / 90 days. Switching it filters
    to that window's Phase 5/6 output; if that window hasn't been
    computed yet, it triggers the one-time clustering + synthesis calls
    described in Phases 5–6 (a short spinner, not a full pipeline
    re-run), then caches the result. Shows the filtered pool size next
    to the selector, and a visible caution notice when a window falls
    under the thin-window guard threshold.
  - The 8 research questions with answers and their linked supporting
    reviews (expandable, so raw text is inspectable — this is where the
    validation requirement becomes visible to the end user, not just to
    the pipeline), scoped to the selected timeframe.
  - Theme browser (Stage 2 output): themes with frequency and example
    reviews, scoped to the selected timeframe.
  - Ad-hoc question box wired to Phase 10's re-query capability, also
    scoped to the selected timeframe.
  - Basic run metadata (review counts at each stage: scraped → filtered
    → sampled → tagged, so funnel drop-off is visible).
- **Visual theme:** styled around Zepto's own brand identity (violet-
  purple primary, white/near-black neutrals, bright accent used
  sparingly for highlights) so the tool visually reads as "about Zepto"
  rather than a generic dashboard — exact palette confirmed against
  Zepto's brand assets before this phase is built, since it couldn't be
  scraped programmatically (site blocks automated fetches).

**Inputs:** JSON outputs from Phases 1–6 (and Phase 10 on demand), read
from the external store described in §3.
**Outputs:** Running Streamlit app.

**Testing:**
- Manual click-through of every view against a real completed run's
  data, in a local `streamlit run` session.
- Manually verify all three timeframes: switching produces the right
  filtered data, first-time selection shows a spinner and triggers
  exactly the expected number of new LLM calls, repeat selection makes
  none.
- Confirm secrets are read via `st.secrets` and the app doesn't crash
  when a key is temporarily rate-limited (failover is invisible to the
  UI).

---

## Phase 9 — Deployment

**What's built:**
- Deployment to Streamlit Community Cloud (or confirmed alternative),
  with the 25–30 Grok keys **and** the external store's credentials
  (see §3) configured as Streamlit secrets in the deployed app's
  settings — neither committed to the repo.
- `requirements.txt` finalized/pinned for the deployed environment.

**Inputs:** Tested app from Phase 8.
**Outputs:** Live deployed URL.

**Testing:**
- Smoke test on the deployed instance: load last saved run, click through
  all views, confirm no missing-secret or import errors.

---

## Phase 10 — Re-query / ad-hoc question capability

**What's built:**
- A single-call query function that takes a new/ad-hoc question,
  pulls relevant tagged reviews (and/or Stage 2 themes) from the already-
  tagged Phase 4 dataset, and answers it with the same
  supporting-reviews-linked format as Stage 3 — **without re-running
  scraping, filtering, sampling, tagging, or clustering.**
- This is why Phase 4's tagged output schema is designed to be
  self-describing/queryable (metadata: date, category, sentiment, theme
  tags per review) from the start.

**Inputs:** `data/tagged/reviews_tagged_<run_date>.json`,
`data/clustered/themes_<run_date>.json`, a free-text question.
**Outputs:** `{question, answer, supporting_reviews}` — same shape as
Stage 3 answers.

**Testing:**
- Real run: ask 2–3 ad-hoc questions not among the original 8 (e.g.,
  "Do users mention discovery via banners/notifications?") against a
  completed Phase 4/5 dataset, manually verify the answer and its cited
  reviews.
- Confirm exactly one LLM call is made per ad-hoc question.

---

## 3. Cross-cutting notes

- **`status.md`** is created in Phase 0 and updated after every phase
  with: current status, what's built, key decisions + rationale, and
  what's next. This is separate from this architecture document and
  will track actual progress/deviations as building proceeds.
- **Git push order:** this architecture document is pushed first, before
  any code. Each subsequent phase's code is pushed only after that phase
  is built and tested, and only after explicit approval to proceed was
  given before starting it.
- **No phase begins — including Phase 0 — until this document is
  explicitly approved.**
- **LLM call budget check:** the main pipeline run — Phase 4 (6–7 calls)
  + Phase 5 @ 90 days (1 call) + Phase 6 @ 90 days (1 call) — is 8–9
  calls, within the 5–10 call target. Selecting the 7-day or 30-day
  timeframe for the first time adds 2 calls each (one Phase 5 + one
  Phase 6 call for that window), computed lazily and cached, so they're
  outside the "full run" budget by design — same treatment as Phase 10's
  ad-hoc queries (1 call each).
- **Storage & persistence.** Streamlit Community Cloud's filesystem is
  ephemeral — anything not committed to git disappears when the app
  sleeps or redeploys, and it re-clones fresh from GitHub on wake. Raw
  and filtered reviews (Phases 1–2) are transient and don't need to
  survive this — local temp files are enough. What *does* need to
  survive is the data Phase 8 shows by default: tagged reviews (Phase 4)
  and each timeframe's themes/synthesis (Phases 5–6). **Implemented in
  Phase 9** as a Supabase Storage bucket (`zepto-discovery-data`),
  synced via `zepto_discovery/external_store.py`: every locally-saved
  file is pushed there right after writing (best-effort — a sync
  failure never blocks a pipeline run), and the app pulls down anything
  missing locally once at startup. Credentials (`SUPABASE_URL`,
  `SUPABASE_KEY`) live in `st.secrets`, same pattern as the Grok keys —
  see `.streamlit/secrets.toml.example`. This is additive, not a
  rewrite: Phases 1–6's own save/load functions are unchanged and still
  read/write local disk exactly as before.
- **Visual identity.** Phase 8's UI is themed around Zepto's own brand
  palette (violet-purple primary + white/near-black neutrals + a bright
  accent for highlights) rather than a generic dashboard look, since the
  tool exists to analyze that specific app. Exact hex values confirmed
  against Zepto's brand assets before Phase 8 starts, if better sources
  than public recollection are available.
