# Status

## Current status

Phases 0-8 complete and tested. Phase 9 (Deployment) is built as far as
it can be from inside this sandbox: pinned dependencies, the Supabase
persistence layer the earlier storage decision was missing, a secrets
template, and a full deployment runbook (`DEPLOYMENT.md`). **The actual
deploy — creating your Supabase project, creating the Streamlit
Community Cloud app, and entering real secrets — is a manual step only
you can do**, detailed step-by-step in `DEPLOYMENT.md`. Everything up to
that point has been built and tested; nothing further can happen here
without your Streamlit/Supabase accounts and real API keys.

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

**Phase 4 (+ Phase 7's key rotation/failover, built alongside it)**
- `zepto_discovery/grok_client.py` — `KeyRotator` round-robins across
  keys, skipping any on cooldown; `call_with_failover()` tries each
  available key in turn, cooling one down on any non-2xx response and
  moving to the next, and raises `AllKeysExhaustedError` (a clear,
  specific error) if every key is cooling down or fails. `load_api_keys()`
  reads `st.secrets["GROK_API_KEYS"]` — a single TOML array of 25-30 keys
  — never hardcoded or from `.env`. `chat_completion()` is the reusable
  entry point Stage 1/2/3 and Phase 10 all call through.
- `zepto_discovery/tagging.py` — `batch_reviews()` splits the sampled set
  into `config.STAGE1_BATCH_SIZE`-sized batches (~6-7 calls at 1,200
  reviews and the current placeholder size of 175); `build_stage1_messages()`
  builds the per-batch prompt; `parse_stage1_response()` validates the
  model's JSON against a required schema (`review_id`, `category`,
  `sentiment` ∈ {positive, negative, neutral, mixed}, `theme_tags` as a
  list) and checks every expected `review_id` came back, raising
  `ValueError` with a specific reason on any mismatch; `tag_batch()`
  retries once (configurable) by appending a correction message to the
  conversation before giving up on a batch; `tag_reviews()` runs every
  batch and merges Stage 1's output back onto each review's original
  `date`/`rating`/`text` metadata.
- `save_tagged()` writes `reviews_tagged_<run_date>.json` to
  `data/tagged/` — this is the schema Phase 5/6/8/10 all read from.

**Phase 5**
- `zepto_discovery/clustering.py` — `filter_by_timeframe()` filters the
  tagged pool by date (no re-sampling) for 7/30/90-day windows;
  `aggregate_tags()` groups Stage 1's raw `theme_tags` by exact text
  (case/whitespace-insensitive) into a frequency-ranked list with a few
  example review IDs each, capped to the top `max_tags` (200 by default)
  to keep the prompt compact; `build_stage2_messages()` prompts for 8-12
  bottom-up themes without ever mentioning the 8 research questions;
  `parse_stage2_response()` validates schema and rejects any theme citing
  a review_id outside the filtered window (catches hallucinated
  citations, not just malformed JSON); `cluster_themes()` retries once on
  a bad response, same pattern as Stage 1.
- `get_or_compute_themes()` is the lazy-cached entry point: loads
  `themes_<run_date>_<timeframe>d.json` from `data/clustered/` if it
  already exists, otherwise computes (1 LLM call) and saves it — this is
  what makes re-selecting an already-computed timeframe in the Phase 8 UI
  free.
- Refactored two small pieces of duplicated logic out to shared modules
  while building this, since Phase 5 needed both: `zepto_discovery/util.py`
  (`parse_iso_datetime`, now used by both `sampler.py` and
  `clustering.py`) and `zepto_discovery/llm_utils.py`
  (`strip_code_fence`, now used by both `tagging.py` and `clustering.py`).
- `tests/test_pipeline_integration.py` — chains real Phase 1-5 functions
  together (starting from `scraper._normalize()`'s actual output shape,
  through `filters.filter_reviews`, `sampler.sample_reviews`,
  `tagging.tag_reviews`, `clustering.cluster_themes`) to prove each
  phase's schema is genuinely consumable by the next, not just correct
  in isolation — confirms date/rating/text survive Phase 1 → 4 → 5
  unchanged, and that every theme's cited review_id is verified against
  the real in-window tagged pool.

**Phase 6**
- `zepto_discovery/synthesis.py` — answers the 8 fixed research
  questions per timeframe from Phase 5's clustered themes. Builds the
  prompt from `_theme_blocks()`, which looks up each theme's cited
  `example_review_ids` in the tagged pool to embed a real excerpt per
  example (this is the actual evidence the model sees, not just an ID).
  `parse_stage3_response()` enforces the validation requirement in the
  schema itself: exactly 8 answers, in a fixed order, each with 3-5
  `supporting_reviews` entries, each a real `{review_id, excerpt, theme}`
  where the `review_id` is checked against the in-window tagged pool —
  a hallucinated or out-of-window citation fails validation and triggers
  the same one-retry-then-error pattern as Stages 1/2.
- **Pre-flight thin-pool guard**: before making any LLM call,
  `synthesize()` checks whether the in-window tagged pool even has 3
  distinct reviews to cite. If not, it raises immediately rather than
  asking the model to satisfy an impossible 3-5-citation requirement
  (which would either fail validation repeatedly or tempt the model
  into fabricating support) — confirmed by a test that asserts the LLM
  is never called in that case.
- `get_or_compute_synthesis()` mirrors Phase 5's lazy-cache pattern
  exactly, writing to `answers_<run_date>_<timeframe>d.json` in
  `data/synthesis/`.
- Extended `tests/test_pipeline_integration.py` to chain all the way
  through Phase 6: `synthesize()` now runs on the real `cluster_themes()`
  output from the same test, for both the full 90-day window and the
  thin 7-day window, confirming every answer is schema-valid (exactly 8
  questions, 3-5 real citations each) in both cases — this is the
  "thin-window guard carries through into Stage 3" test the
  architecture doc specifically calls for.

**Phase 8**
- `zepto_discovery/run_data.py` — pure-Python data-loading layer between
  disk and the UI, kept separate from `app/main.py` so it's unit-testable
  without a Streamlit runtime: `latest_run_date()` (most recent run with
  completed tagging), `funnel_counts()` (per-stage review counts),
  `load_tagged_reviews()`, `load_timeframe_view()` (loads cached
  themes/answers for a timeframe if present), and
  `ensure_timeframe_computed()` (computes+caches whichever of
  themes/answers is missing for a timeframe, reusing Phase 5/6's
  `get_or_compute_*` functions directly).
- `app/main.py` — full UI: sidebar with a "Run new pipeline" button
  (runs the real Phase 1-4 chain, wrapped so any failure shows a clean
  error instead of crashing); funnel metrics (scraped → filtered →
  sampled → tagged); a 7/30/90-day radio selector that computes a
  timeframe on first selection (with a spinner) and reuses the cache on
  repeat selection; the 8 research questions as expandable panels
  showing the answer and its 3-5 supporting reviews (theme, excerpt,
  review_id); a theme browser (expandable, showing description +
  example review IDs); and an ad-hoc question box that's present but
  **intentionally disabled** with a "Coming in Phase 10" note — Phase 10
  (re-query) hasn't been built yet, so this box isn't wired to anything
  real rather than half-implementing that phase early.
- `.streamlit/config.toml` — Zepto's violet-purple theme
  (`primaryColor = "#7C1FE0"`), the same palette used in the earlier
  architecture diagram, applied via Streamlit's standard theme config
  rather than custom CSS injection.

**Phase 9**
- `zepto_discovery/external_store.py` — resolves the persistence gap
  flagged (but deferred) back in Phase 5/6: mirrors `config.DATA_DIR`
  into a Supabase Storage bucket via its REST API directly through
  `requests` (same pattern as `grok_client.py`, no extra SDK dependency).
  `upload_file()`/`download_file()`/`list_remote_files()` raise clear
  errors and are unit-tested against an injectable fake session;
  `sync_up()`/`sync_down_all()` are the best-effort wrappers actually
  used by the app — they swallow any failure and return a status value
  instead of raising, since losing durability is an acceptable
  degradation but crashing the app is not.
- Made `clustering.themes_path()` and `synthesis.answers_path()` public
  (were private, `_themes_path`/`_answers_path`) so `app/main.py` can
  sync those specific files without reaching into another module's
  internals.
- Wired into `app/main.py`: `sync_down_all()` runs once per session at
  startup (pulls back anything missing locally after a restart);
  `sync_up()` runs after every local save in `run_full_pipeline()` and
  after computing a new timeframe.
- `requirements.txt` pinned to exact installed versions
  (`streamlit==1.60.0`, `google-play-scraper==1.2.7`,
  `requests==2.33.1`, `pytest==9.1.1`) for deployment reproducibility.
- `.streamlit/secrets.toml.example` documents the required secrets
  (`GROK_API_KEYS`, `SUPABASE_URL`, `SUPABASE_KEY`) without containing
  real values.
- `DEPLOYMENT.md` — a step-by-step runbook for the manual parts only the
  user can do (Supabase project + bucket creation, Streamlit Community
  Cloud app creation, entering real secrets), plus a smoke-test
  checklist matching `ARCHITECTURE.md`'s Phase 9 testing requirement.

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
- **`GROK_API_KEYS` as a single TOML array secret**, not 25-30 separate
  numbered secrets — simpler to load and rotate over, and matches
  `ARCHITECTURE.md`'s note that the exact key naming convention would be
  decided during this phase.
- **A non-2xx response of any kind triggers failover**, not just 429 —
  the architecture doc says "rate-limit or error," and treating any
  failure the same way keeps the failover logic simple; a key that's
  actually broken just cools down and gets retried on the next call
  rather than blocking the batch.
- **Malformed-JSON retry is a single follow-up turn in the same
  conversation** (appends the bad response + a correction request),
  not a fresh prompt from scratch — gives the model its own mistake to
  correct against, which is usually more reliable than a cold retry.
- **Caching is a local-disk cache keyed by (run_date, timeframe_days)**,
  checked before any LLM call is made. This satisfies the "lazy,
  cached" requirement now, independent of the external-store decision in
  `ARCHITECTURE.md` §3 (which is a Phase 8/9 deployment concern for
  surviving a Streamlit restart) — the two aren't the same thing, and
  Phase 5 only needed the former to be correct and testable.
- **Stage 2's schema validation rejects any theme citing a review_id
  outside the filtered timeframe**, not just malformed JSON — a
  hallucinated or out-of-window citation would otherwise silently break
  the "every insight traces to 3-5 real source reviews" requirement
  further downstream in Stage 3.
- **Tags are aggregated by exact text match, not fuzzy/semantic
  grouping** — the near-duplicate-phrase problem this creates (e.g.
  "late delivery" vs. "delivery delay" as separate entries) is left for
  the LLM to resolve during clustering, per `ARCHITECTURE.md`'s
  "aggregated tags, not raw text" instruction; doing semantic grouping
  ourselves first would pre-empt the bottom-up taxonomy the spec
  explicitly wants to avoid.
- **A thin-pool preflight check happens before the LLM call, not after**
  — mirrors the philosophy behind Phase 1's empty-first-page guard:
  don't spend a call (or multiple retries) on a request that's
  structurally guaranteed to fail its own validation, and don't give the
  model a reason to invent evidence just to hit a required count.
- **Stage 3's evidence is looked up from the tagged pool, not just
  passed through from Stage 2** — `_theme_blocks()` re-fetches each
  cited review's actual text to build the excerpt shown to the model.
  This means the model is answering from real quoted text, not just a
  bare ID, and keeps Stage 2 and Stage 3 loosely coupled (Stage 3 only
  needs a theme's name/description/count/example_review_ids, not
  Stage 2's internal aggregation details).
- **The ad-hoc question box is built but deliberately disabled**, not
  skipped and not half-wired to a stub — visible in the UI so the shape
  of the eventual feature is clear, but not implemented, since Phase 10
  hasn't been approved yet.
- **The "Run new pipeline" button runs the real Phase 1-4 chain**, not
  a mocked demo path — it's wrapped in a try/except that surfaces
  whatever error comes back (missing secrets, network failure, Grok
  errors) as a clean sidebar message instead of a crash, since this
  environment can't run it successfully end-to-end anyway (see below)
  and a real deployment needs this failure path to work regardless.
- **Streamlit's own `[theme]` config**, not raw CSS injection, for the
  Zepto brand palette — simpler, and Streamlit already re-applies it
  consistently across all built-in widgets (radio buttons, buttons,
  expanders) rather than needing every component individually restyled.
- **The external store is additive, implemented as a thin sync layer in
  `app/main.py`, not a rewrite of Phases 1-6's save/load functions.**
  Every phase's existing, already-tested `save_*`/`load_*` functions are
  untouched; `sync_up()`/`sync_down_all()` just mirror whatever's on
  local disk to/from the bucket around the existing calls. Lower
  risk than threading Supabase calls through six already-working
  modules, at the cost of the sync living in the UI layer rather than
  inside each phase itself.
- **Sync failures are silent by design** (`sync_up`/`sync_down_all`
  return a status value, never raise) — durability surviving a
  Streamlit restart is a nice-to-have; a pipeline run or page load that
  otherwise succeeded must never fail because of it.
- **Storage REST calls via `requests`, not the `supabase-py` SDK** —
  consistent with how `grok_client.py` is built, avoids a heavier
  dependency, and keeps the same injectable-session testing pattern
  used everywhere else in this codebase. The exact request/response
  shape is my best understanding of Supabase's Storage API and hasn't
  been verified against a real project (`supabase.com` is blocked by
  this sandbox's network policy, same as `play.google.com`/`api.x.ai`)
  — flagged explicitly in `DEPLOYMENT.md`'s smoke-test checklist as the
  first real-world check.

## Testing performed

- `python3 -m pytest -v` — 109/109 tests pass (5 config + 8 scraper + 15
  filters + 8 sampler + 11 grok_client + 13 tagging + 17 clustering + 15
  synthesis + 8 run_data + 8 external_store + 1 full Phase 1-6
  integration test).
- `python3 -m py_compile` on every module — compiles cleanly.
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
- **Both `play.google.com` and `api.x.ai` are blocked by this build
  sandbox's outbound network policy** (confirmed the same way as Phase
  1's finding: a policy-level 403 on the proxy's own status log). This
  means the two remaining real-network steps `ARCHITECTURE.md` calls for
  in Phase 4 — empirically confirming the Stage 1 batch size against
  Grok's actual JSON reliability, and the one-real-call integration
  check for `grok_client.py` — **cannot be done in this environment
  regardless of whether real API keys are supplied here**. Both need to
  run somewhere with real internet access before Phase 4 is considered
  fully verified.
- **Phase 4 spot-check** ran the complete `tag_reviews()` pipeline
  end-to-end (batching → prompt → JSON parse/validate → metadata merge)
  against 10 hand-written reviews using a fake, keyword-based stand-in
  for the LLM (same synthetic-data caveat as Phases 1-3), with
  `batch_size=2` forcing multiple batches. All 10 came back correctly
  tagged, batched as expected (4/4/2), and merged with their original
  date/rating/text intact. This validates the pipeline machinery; it
  does not validate real Grok output quality, which needs the pending
  real-network run above.
- **Phase 5 spot-check** generated a synthetic tagged pool of 400
  reviews across 10 realistic theme categories (delivery speed, produce
  quality, customer support, app stability, pricing, category discovery,
  packaging, notifications, staff behavior, dark store availability),
  spread over 90 days with more volume in recent days, and ran
  `get_or_compute_themes()` for all three timeframes through a
  keyword-based clustering stand-in (same synthetic-data caveat as
  Phases 1-4). Results: pool sizes correctly shrank with the window
  (400 -> 298 -> 127), all three windows landed at exactly 10 themes
  (within the 8-12 expected range) with no near-empty catch-all bucket,
  and exactly 3 LLM calls were made total — one per fresh timeframe.
  Re-selecting the already-computed 90-day window afterward made 0 new
  calls, confirming the cache works. Manually traced 3 example
  review_ids per theme (30 total) back to their actual tagged content —
  all 30 correctly belonged to the theme that cited them. In this
  particular synthetic distribution the 7-day window had enough volume
  (127 reviews) to stay above the thin-window threshold, so the guard
  didn't trip here — its logic is separately confirmed by a dedicated
  unit test with a deliberately sparse pool; whether it trips on the
  real 90-day sample depends on Zepto's actual review volume, unknowable
  until the pending real-network run happens.
- **Phase 6 spot-check** extended the same synthetic 400-review, 10-theme
  pool from the Phase 5 spot-check through `get_or_compute_synthesis()`
  for all three timeframes, using a keyword-driven stand-in that answers
  each research question by citing real reviews from the theme most
  relevant to it. Manually read through the first 3 of 8 answers per
  timeframe (9 answers, 27 citations total) and traced every cited
  excerpt back to its actual source review's text — all 27 matched
  exactly. Pool sizes and thin-window flags carried through from Phase 5
  unchanged (400/298/127 across the three windows).
- **Integration confirmed, not just assumed**: `test_pipeline_integration.py`
  now runs actual Phase 1 (`scraper._normalize`) -> Phase 2
  (`filter_reviews`) -> Phase 3 (`sample_reviews`) -> Phase 4
  (`tag_reviews`) -> Phase 5 (`cluster_themes`) -> Phase 6 (`synthesize`)
  in one chain, for both a normal and a thin timeframe, and asserts the
  schema handoffs hold at every step — not just that each phase works
  alone.
- **Phase 8 was actually launched and driven in a real browser**, not
  just unit-tested: generated a synthetic completed run (500 reviews
  through the real Phase 1-6 modules), started the app with
  `streamlit run app/main.py`, and drove it headlessly via Playwright
  against the pre-installed Chromium. Confirmed: the funnel metrics
  show correctly (500/500/500/500); all three timeframe radio buttons
  switch and show correctly different pool sizes (500 / 359 / 150) and
  theme counts; expanding a research question shows its answer and 3
  supporting reviews with theme/excerpt/review_id; expanding a theme
  shows its description and example review IDs; the Zepto violet-purple
  theme renders throughout (selected radio buttons, headers). No console
  page errors — the only console output was Streamlit's own telemetry
  beacon failing, which is this sandbox's network policy blocking
  Streamlit's phone-home, unrelated to the app.
- **Cross-phase integration caught live, not just in tests**: clicking
  "Run new pipeline" triggers a real Phase 1 scrape attempt, which fails
  against this sandbox's blocked network — and Phase 1's empty-first-page
  guard (built in an earlier session) fired correctly, surfacing its
  exact error message as a clean sidebar banner instead of crashing the
  app, with the existing good demo data left completely undisturbed
  underneath it. This is Phase 1 and Phase 8 working together correctly
  under a real failure, not two phases that merely both pass in
  isolation.
- One gap not yet exercised in the browser: the thin-window caution
  banner (`is_thin`) — none of the three demo timeframes happened to
  fall under the 50-review threshold. The underlying flag is unit-tested
  directly; only the one-line `st.warning(...)` rendering itself wasn't
  screenshotted.
- **Phase 9 testing, within this sandbox's limits**: pinned
  `requirements.txt` re-installs cleanly with no resolver conflicts.
  Re-launched the app in a real browser with `external_store` wired in —
  renders identically to before (Supabase unreachable here, so
  `sync_down_all()`/`sync_up()` correctly no-op rather than breaking
  anything). Simulated a genuinely fresh deploy (emptied `data/` back to
  just `.gitkeep`s, removed any local `secrets.toml`) and confirmed the
  app boots cleanly to an empty-state message — no missing-secret or
  import error on load, matching `ARCHITECTURE.md`'s smoke-test
  requirement. Clicked "Run new pipeline" in that fresh state too: fails
  on the network block as expected, with the same clean sidebar error as
  before (the code never reaches the point where a missing
  `GROK_API_KEYS` secret would be the reported error, since the scrape
  step fails first every time in this sandbox — noted honestly rather
  than claimed as verified).
- **What Phase 9 cannot include from inside this sandbox**: creating the
  actual Supabase project/bucket, creating the actual Streamlit
  Community Cloud app, entering real secrets, and the resulting
  real-world smoke test (`DEPLOYMENT.md`'s checklist) — all of that
  needs the user's own accounts and is documented step-by-step there
  instead of attempted here.

## What's next

Deployment itself (the manual steps in `DEPLOYMENT.md`) is up to the
user now — nothing further to build there without real accounts/keys.
Pending the user's go-ahead: Phase 10 (re-query), which is the only
remaining phase in `ARCHITECTURE.md`. Also still outstanding, same as
every phase since Phase 1: real-network runs of the scraper and Grok
client (including the batch-size confirmation), and re-running the
Phase 2-6 spot-checks against that real data once available — all
achievable once the app is actually deployed per `DEPLOYMENT.md`.
