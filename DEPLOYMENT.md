# Deployment runbook

Everything in this file is a manual step only you can do — it needs
your own Streamlit Community Cloud account, your own Supabase account,
and your own Grok API keys. This document exists so those steps are
precise and repeatable; I can't click through them for you.

## 1. Create the Supabase project (persistence)

1. Sign up at [supabase.com](https://supabase.com) (free tier is fine —
   500MB database, 1GB storage; the one caveat: a free project
   auto-pauses after 7 days with no API activity and needs a manual
   "Restore" click in the dashboard to wake back up).
2. Create a new project.
3. In that project, go to **Storage** and create a new bucket named
   exactly `zepto-discovery-data` (this name is hardcoded as `BUCKET` in
   `zepto_discovery/external_store.py`).
4. Go to **Settings → API** and copy:
   - **Project URL** → this is `SUPABASE_URL`
   - **service_role key** (or `anon` key if you've set bucket policies
     to allow anon read/write) → this is `SUPABASE_KEY`

## 2. Get Grok API keys

You mentioned you'll provide 25-30 Grok (xAI) API keys. Gather them into
a plain list — order doesn't matter, `grok_client.py` rotates across
them automatically.

## 3. Local secrets file (for testing before you deploy)

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Edit `.streamlit/secrets.toml` and fill in `GROK_API_KEYS`,
`SUPABASE_URL`, and `SUPABASE_KEY` with the real values from steps 1-2.
This file is gitignored — it will never be committed.

Then run one real end-to-end check locally, from the repo root:

```bash
pip install -r requirements.txt
streamlit run app/main.py
```

Click **Run new pipeline** in the sidebar. This is the first point
where the app talks to the real Play Store and real Grok — everything
up to now was verified against synthetic data in a network-blocked
sandbox, so this is the actual real-world test the earlier phases
flagged as outstanding.

## 4. Deploy to Streamlit Community Cloud

1. Push this repo to GitHub if it isn't already (it is — branch
   `claude/zepto-discovery-engine-spec-fb45j5`).
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in, and
   click **New app**.
3. Point it at this repository, the branch above (or `main` once
   merged), and `app/main.py` as the entry point.
4. Before or after the first deploy, open the app's **Settings →
   Secrets** and paste in the same contents as your local
   `.streamlit/secrets.toml` (steps 1-3) — `GROK_API_KEYS`,
   `SUPABASE_URL`, `SUPABASE_KEY`.
5. Deploy.

## 5. Smoke test the deployed app

This is the test `ARCHITECTURE.md`'s Phase 9 section calls for:

- [ ] App loads without a missing-secret or import error.
- [ ] If a previous run's data exists in the Supabase bucket, it appears
      after the first load (confirms `sync_down_all()` works against
      the real store, not just the mocked tests).
- [ ] Click **Run new pipeline** once, successfully, against the real
      Play Store and real Grok API.
- [ ] Switch between all three timeframes; confirm the first switch to
      an uncomputed window shows a spinner and produces real Grok
      output, and switching back is instant (no re-computation).
- [ ] Put the app to sleep (or manually restart it from the Streamlit
      Cloud dashboard) and reload — confirm the last run's data is
      still there (this is the actual test that persistence works, not
      just that it doesn't crash).
- [ ] Read through a few research-question answers against their cited
      supporting reviews for real Grok output quality — this is also
      the first point where the batch-size and prompt-quality questions
      flagged in earlier phases can actually be answered.
