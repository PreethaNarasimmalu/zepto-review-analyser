# Status

## Current status

Phase 0 (repo scaffolding), Phase 1 (scraping), Phase 2 (filtering), and
Phase 3 (sampling) complete and tested. Starting Phase 4 (Stage 1
tagging) next in this same session.

## What's been built

**Phase 0**
- Repo skeleton: `zepto_discovery/` (config constants), `app/` (Streamlit
  entry point placeholder), `tests/`, and
  `data/{raw,filtered,sampled,tagged,clustered,synthesis}/` (contents
  gitignored, directories tracked via `.gitkeep`).
- `zepto_discovery/config.py` centralizes every constant later phases
  need: Play Store app id/locale, the 90-day time window, the 7/30/90-day
  timeframe options, the 1,200 review sample cap, the thin-window
  evidence threshold, and a placeholder Stage 1 batch size.
- `.gitignore` excludes pipeline data directories' contents and any
  Streamlit secrets file.
- `tests/test_config.py` — 5 tests covering config values and that the
  expected directory structure exists.

**Phase 1**
- `zepto_discovery/scraper.py` — pulls Play Store reviews for
  `com.zeptoconsumerapp` via `google-play-scraper`, paginating
  newest-first and stopping once a page crosses the configured time
  window (default 90 days from run date). Normalizes each review to
  `{review_id, text, rating, date, thumbs_up}` — author is dropped
  entirely, not just hashed.
- `save_raw()` writes the result to `data/raw/reviews_raw_<run_date>.json`.
- `tests/test_scraper.py` — 8 tests covering the pagination/boundary
  logic (including exact-cutoff and one-day-past-cutoff edge cases) via
  an injectable `fetch_page` seam, plus the save path.

**Phase 2**
- `zepto_discovery/filters.py` — four independent, testable drop rules:
  `is_emoji_only`, `is_rating_restatement`, `is_too_short` (< 3 words),
  `is_generic` (word-count-independent: flags reviews with no token
  outside a stoplist/generic-adjective/generic-noun set, so a long review
  made entirely of filler still gets caught, not just short ones).
  `is_rating_restatement` strips a detected star-rating phrase (e.g. "5
  stars", "one star") and then reuses the generic-content check on
  what's left, so "3 stars, good app but expensive" is correctly kept
  (mentions "expensive") while "1 star worst app" is dropped.
- `filter_reviews()` runs the rules in a fixed order and records which
  rule dropped each review (at most one reason per review) — this is the
  audit trail `ARCHITECTURE.md` asks for.
- `save_filtered()` writes `reviews_filtered_<run_date>.json` and
  `drop_log_<run_date>.json` to `data/filtered/`.

**Phase 3**
- `zepto_discovery/sampler.py` — stratifies filtered reviews by
  `(star rating, recency chunk)` (3 chunks across the 90-day window),
  then allocates the 1,200-review cap proportionally across strata using
  a largest-remainder method (so the cap is hit exactly, not just
  approximately, while never allocating more than a stratum actually
  has).
- Deterministic: a fixed seed (`config.RANDOM_SEED = 42`, overridable)
  drives `random.sample()` within each stratum, and each stratum's pool
  is sorted by `review_id` before sampling so the same input always
  produces the same output.
- If the eligible pool is at or under the cap, every review is kept and
  `used_full_pool` is set in the report.
- `save_sampled()` writes `reviews_sampled_<run_date>.json` and
  `sample_report_<run_date>.json` (per-stratum eligible vs. sampled
  counts) to `data/sampled/`.

## Key decisions taken (and why)

- **Flat package layout** (`zepto_discovery/` at repo root, not
  `src/zepto_discovery/`) so `python -m pytest` resolves imports without
  needing an editable install step first — matches the Phase 0 test
  requirement in `ARCHITECTURE.md` as written.
- **`data/` subdirectory contents are gitignored**, tracked only via
  `.gitkeep` per subdirectory. Raw/filtered/sampled data is locally
  transient by design (Phase 1–3); tagged/clustered/synthesis outputs are
  persisted to the external store decided in `ARCHITECTURE.md` §3, not to
  git.
- **`STAGE1_BATCH_SIZE` is a placeholder (175)** — flagged in the config
  file itself as pending the empirical confirmation `ARCHITECTURE.md`
  calls for during Phase 4, not a real decision yet.
- **Zero reviews on the first page is treated as an error, not a valid
  result.** While building this phase, inspecting `google_play_scraper`'s
  source showed it silently swallows network/parsing exceptions inside
  its own retry loop and just returns whatever it collected — which can
  be an empty list with no error raised at all. A live grocery-delivery
  app realistically always has some reviews, so an empty *first* page is
  almost certainly a masked failure (blocked network, rate limit, changed
  response format), not a genuine zero-review result. `scrape_reviews()`
  now raises `RuntimeError` in that specific case rather than letting a
  failed scrape silently look like a successful empty run. An empty page
  after some reviews have already been collected is still treated as a
  normal "ran out of reviews" stop.
- **Rule check order puts the more specific reasons first**
  (`emoji_only`, `rating_restatement`, then `too_short`, then `generic`).
  A pure-emoji review or a bare "5 stars" is technically also
  "too short," but logging it under `too_short` would be a less useful
  audit trail than logging its actual, more specific reason — found this
  while writing the combined-pipeline test, which initially asserted the
  wrong label for exactly this reason.
- **`is_generic` is intentionally a stoplist/blacklist, not a
  whitelist.** It only flags a review if *every* token is in a small
  generic-adjective/generic-noun/stopword set — anything else (a
  product name, a category, a specific complaint) keeps it. This means
  some genuinely vague reviews slip through uncaught (see testing notes
  below) in exchange for near-zero risk of dropping a substantive one —
  the safer failure mode for a pre-LLM filter that isn't the final
  quality bar.
- **Largest-remainder allocation, not simple rounding**, for splitting
  the 1,200 cap across up to 15 strata — plain `round()` per stratum can
  under- or over-shoot the cap by a few reviews when summed; largest
  remainder guarantees the total lands exactly on the cap (or on the
  pool size, if smaller).

## Testing performed

- `python3 -m pytest -v` — 36/36 tests pass (5 config + 8 scraper + 15
  filters + 8 sampler).
- `python3 -m py_compile app/main.py` / `scraper.py` / `filters.py` /
  `sampler.py` — compile cleanly.
- Manually verified `.gitignore` behavior: a scratch file dropped into
  `data/raw/` is correctly ignored by `git status`/`git add -A`, while
  each directory's `.gitkeep` is tracked.
- **Real-run scraping could not be completed in this build environment**:
  this sandbox's outbound network policy blocks `play.google.com`
  (confirmed via the proxy's own status endpoint — a policy-level 403,
  not a bug in the scraper). Ran `python -m zepto_discovery.scraper`
  against this to confirm the new zero-results guard actually fires
  end-to-end: it raised the expected `RuntimeError` and exited non-zero,
  and no file was written to `data/raw/` — i.e. the failure mode is loud,
  not silent. **The actual "pull real Zepto reviews and manually inspect
  ~20 of them" verification from `ARCHITECTURE.md` still needs to run
  somewhere with real internet access** — locally, or once deployed —
  before Phase 1 can be considered fully verified against real data.
- **Phase 2 spot-check** ran `filter_reviews()` against ~41 hand-written
  review-style examples (mix of English/Hinglish, emoji, star-mentions,
  substantive praise/complaints) since Phase 1 has no real scraped data
  to run this against yet in this environment. Result: 24 kept / 17
  dropped. Manually reviewed both lists — no substantive review was
  wrongly dropped (no over-filtering found). One known gap: `"waste of
  time"` was kept despite being about as content-free as `"worst app"`,
  because "waste" and "time" aren't in the generic-word lists — accepted
  as a minor, safe-direction miss rather than tuned away (see key
  decisions above). This is a synthetic stand-in for the real
  ~30-kept/~30-dropped spot-check `ARCHITECTURE.md` calls for — it still
  needs to be re-run against actual filtered Zepto reviews once Phase 1
  has real data.
- **Phase 3 spot-check** ran `sample_reviews()` against a synthetic pool
  of 2,000 reviews with a realistic ratings skew (45% 5-star, 20% 4-star,
  10% 3-star, 10% 2-star, 15% 1-star, uniformly spread across the 90-day
  window) — same stand-in-for-real-data caveat as Phases 1–2. Result: all
  15 strata sampled at ~60% (1,200/2,000) within a fraction of a percent
  of each other; 1-star (305→182) and 5-star (923→554) both represented
  proportional to their pool share, not artificially balanced; all 3
  recency chunks present in the sample. No anomalies found.

## What's next

Phase 4 — Stage 1 Tagging, building now. Also still pending: a
real-network run of Phase 1's scraper (to get real data), and re-running
the Phase 2/3 spot-checks against that real data once available.
