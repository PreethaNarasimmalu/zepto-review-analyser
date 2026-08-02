# Status

## Current status

Phase 0 (repo scaffolding) and Phase 1 (scraping) complete and tested.
Waiting on explicit approval before starting Phase 2 (filtering).

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

## Testing performed

- `python3 -m pytest -v` — 13/13 tests pass (5 config + 8 scraper).
- `python3 -m py_compile app/main.py` / `scraper.py` — compile cleanly.
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

## What's next

Phase 2 — Filtering, pending explicit approval to start. Also pending: a
real-network run of Phase 1's scraper to confirm actual review content,
volume, and field shape look correct (see testing note above).
