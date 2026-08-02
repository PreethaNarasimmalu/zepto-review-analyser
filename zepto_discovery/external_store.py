"""Optional external persistence layer for Streamlit Community Cloud,
where local disk doesn't survive a sleep/redeploy — anything not synced
out disappears and the app re-clones fresh from git on wake.

This is an add-on, not a replacement: every phase's save_*/load_*
function still reads/writes local disk exactly as before (Phases 1-6 are
unchanged). app/main.py calls sync_down_all() once at startup (pulling
back anything missing locally after a restart) and sync_up() right after
writing a new local file, both of which degrade to "local disk only"
rather than raising if the store isn't configured or reachable — losing
durability across restarts is an acceptable degradation; crashing the
app is not.

Talks to Supabase Storage's REST API directly via `requests` (same
pattern as grok_client.py) rather than pulling in the full supabase-py
SDK. Exact endpoint/header behavior should be verified against a real
Supabase project during deployment — this sandbox's network policy
blocks supabase.com, so only the injectable-client unit tests below
could be run here.
"""

from pathlib import Path

import requests

from zepto_discovery import config

BUCKET = "zepto-discovery-data"


def _get_credentials():
    import streamlit as st

    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
    except Exception as e:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_KEY not found in Streamlit secrets. "
            "Add them to .streamlit/secrets.toml (local) or the deployed "
            "app's Secrets settings — see .streamlit/secrets.toml.example."
        ) from e
    return url.rstrip("/"), key


def _headers(key):
    return {"Authorization": f"Bearer {key}", "apikey": key}


def _relative_path(local_path):
    return Path(local_path).resolve().relative_to(config.DATA_DIR.resolve()).as_posix()


def upload_file(local_path, session=None, credentials=None):
    """Uploads local_path's contents to the bucket at the path relative
    to config.DATA_DIR (e.g. data/tagged/x.json -> tagged/x.json)."""
    session = session or requests
    url, key = credentials or _get_credentials()
    rel_path = _relative_path(local_path)
    content = Path(local_path).read_bytes()

    response = session.post(
        f"{url}/storage/v1/object/{BUCKET}/{rel_path}",
        headers={**_headers(key), "x-upsert": "true", "Content-Type": "application/json"},
        data=content,
        timeout=30,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Upload failed for {rel_path}: {response.status_code} {response.text[:200]}")
    return rel_path


def list_remote_files(prefix="", session=None, credentials=None):
    session = session or requests
    url, key = credentials or _get_credentials()
    response = session.post(
        f"{url}/storage/v1/object/list/{BUCKET}",
        headers={**_headers(key), "Content-Type": "application/json"},
        json={"prefix": prefix, "limit": 1000, "offset": 0},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Listing failed: {response.status_code} {response.text[:200]}")
    return [item["name"] for item in response.json() if item.get("name")]


def download_file(rel_path, session=None, credentials=None):
    session = session or requests
    url, key = credentials or _get_credentials()
    response = session.get(
        f"{url}/storage/v1/object/{BUCKET}/{rel_path}",
        headers=_headers(key),
        timeout=30,
    )
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise RuntimeError(f"Download failed for {rel_path}: {response.status_code} {response.text[:200]}")
    return response.content


def sync_up(local_path, session=None, credentials=None):
    """Best-effort push of one local file. Returns True on success, False
    (never raises) if the store isn't configured or reachable — a failed
    upload must never break a pipeline run that otherwise succeeded."""
    try:
        upload_file(local_path, session=session, credentials=credentials)
        return True
    except Exception:
        return False


def sync_down_all(session=None, credentials=None):
    """Best-effort pull of every remote file not already present locally.
    Returns the count downloaded; returns 0 (never raises) if the store
    isn't configured or reachable, so app startup never breaks over this."""
    try:
        remote_names = list_remote_files(session=session, credentials=credentials)
    except Exception:
        return 0

    downloaded = 0
    for rel_path in remote_names:
        local_path = config.DATA_DIR / rel_path
        if local_path.exists():
            continue
        try:
            content = download_file(rel_path, session=session, credentials=credentials)
        except Exception:
            continue
        if content is None:
            continue
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content)
        downloaded += 1
    return downloaded
