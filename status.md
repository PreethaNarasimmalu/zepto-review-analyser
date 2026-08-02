# Status

## Current status

Phase 0 (repo scaffolding), Phase 1 (scraping), Phase 2 (filtering),
Phase 3 (sampling), Phase 4 (Stage 1 tagging + the key rotation/failover
client from Phase 7), and Phase 5 (Stage 2 clustering) complete and
tested — including a full Phase 1-5 integration test chaining the real
functions together, not just isolated unit tests. Waiting on explicit
approval before starting Phase 6 (Stage 3 synthesis).

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

## Testing performed

- `python3 -m pytest -v` — 78/78 tests pass (5 config + 8 scraper + 15
  filters + 8 sampler + 11 grok_client + 13 tagging + 17 clustering + 1
  full Phase 1-5 integration test).
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
- **Integration confirmed, not just assumed**: `test_pipeline_integration.py`
  runs actual Phase 1 (`scraper._normalize`) -> Phase 2 (`filter_reviews`)
  -> Phase 3 (`sample_reviews`) -> Phase 4 (`tag_reviews`) -> Phase 5
  (`cluster_themes`) in one chain and asserts the schema handoffs hold at
  every step, not just that each phase works alone.

## What's next

Phase 6 — Stage 3 Synthesis, pending explicit approval to start. Also
still pending: real-network runs of Phase 1's scraper and Phase 4's Grok
client/batch-size confirmation, and re-running the Phase 2/3/4/5
spot-checks against that real data once available.
