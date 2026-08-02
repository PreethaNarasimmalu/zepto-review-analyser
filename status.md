# Status

## Current status

Phase 0 (repo & project scaffolding) complete, tested, and pushed. Waiting
on explicit approval before starting Phase 1 (scraping).

## What's been built

- Repo skeleton: `zepto_discovery/` (config constants), `app/` (Streamlit
  entry point placeholder), `tests/`, and
  `data/{raw,filtered,sampled,tagged,clustered,synthesis}/` (contents
  gitignored, directories tracked via `.gitkeep`).
- `zepto_discovery/config.py` centralizes every constant later phases
  need: Play Store app id/locale, the 90-day time window, the 7/30/90-day
  timeframe options, the 1,200 review sample cap, the thin-window
  evidence threshold, and a placeholder Stage 1 batch size.
- `requirements.txt` (just `pytest` for now — other dependencies get
  added phase by phase, as each one actually needs them).
- `.gitignore` excludes pipeline data directories' contents and any
  Streamlit secrets file.
- `tests/test_config.py` — 5 tests covering config values and that the
  expected directory structure exists.

## Key decisions taken (and why)

- **Flat package layout** (`zepto_discovery/` at repo root, not
  `src/zepto_discovery/`) so `python -m pytest` resolves imports without
  needing an editable install step first — this matches the Phase 0 test
  requirement in `ARCHITECTURE.md` as written.
- **`data/` subdirectory contents are gitignored**, tracked only via
  `.gitkeep` per subdirectory. Raw/filtered/sampled data is locally
  transient by design (Phase 1–3); tagged/clustered/synthesis outputs are
  persisted to the external store decided in `ARCHITECTURE.md` §3, not to
  git — keeping the repo clean while still giving every phase a
  pre-existing directory to write into.
- **`STAGE1_BATCH_SIZE` is a placeholder (175)** — flagged in the config
  file itself as pending the empirical confirmation `ARCHITECTURE.md`
  calls for during Phase 4, not a real decision yet.

## Testing performed

- `python3 -m pytest -v` — 5/5 tests pass.
- `python3 -m py_compile app/main.py` — compiles cleanly.
- Manually verified `.gitignore` behavior: a scratch file dropped into
  `data/raw/` is correctly ignored by `git status`/`git add -A`, while
  each directory's `.gitkeep` is tracked.
- `git add -A` reviewed by hand before commit — only the intended
  scaffolding files staged, no stray data.

## What's next

Phase 1 — Scraping, pending explicit approval to start.
