import json

from zepto_discovery import config
from zepto_discovery.external_store import (
    download_file,
    list_remote_files,
    sync_down_all,
    sync_up,
    upload_file,
)

CREDS = ("https://fake-project.supabase.co", "fake-key")


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else []
        self.content = content
        self.text = content.decode() if isinstance(content, bytes) else str(content)

    def json(self):
        return self._json_data


class FakeSession:
    """Records calls and serves canned responses, mirroring the FakeResponse
    pattern used throughout grok_client/tagging/clustering tests."""

    def __init__(self):
        self.calls = []
        self.store = {}  # rel_path -> bytes, simulating the remote bucket

    def post(self, url, headers=None, data=None, json=None, timeout=None):
        self.calls.append(("post", url, headers, data, json))
        if "/object/list/" in url:
            names = sorted(self.store.keys())
            return FakeResponse(200, json_data=[{"name": n} for n in names])
        # upload
        rel_path = url.split(f"/object/", 1)[1].split("/", 1)[1]
        self.store[rel_path] = data
        return FakeResponse(200)

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("get", url, headers))
        rel_path = url.split(f"/object/", 1)[1].split("/", 1)[1]
        if rel_path not in self.store:
            return FakeResponse(404)
        return FakeResponse(200, content=self.store[rel_path])


# --- upload_file -------------------------------------------------------


def test_upload_file_posts_relative_path_and_content(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    local_file = tmp_path / "tagged" / "reviews_tagged_2026-08-02.json"
    local_file.parent.mkdir(parents=True)
    local_file.write_text(json.dumps([{"review_id": "r1"}]))

    session = FakeSession()
    rel_path = upload_file(local_file, session=session, credentials=CREDS)

    assert rel_path == "tagged/reviews_tagged_2026-08-02.json"
    assert rel_path in session.store
    assert b"r1" in session.store[rel_path]


def test_upload_file_raises_on_error_status(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    local_file = tmp_path / "x.json"
    local_file.write_text("{}")

    class FailingSession:
        def post(self, *a, **kw):
            return FakeResponse(500, content=b"server error")

    import pytest

    with pytest.raises(RuntimeError, match="Upload failed"):
        upload_file(local_file, session=FailingSession(), credentials=CREDS)


# --- list_remote_files / download_file -----------------------------------


def test_list_and_download_round_trip():
    session = FakeSession()
    session.store["tagged/x.json"] = b'{"a": 1}'

    names = list_remote_files(session=session, credentials=CREDS)
    assert names == ["tagged/x.json"]

    content = download_file("tagged/x.json", session=session, credentials=CREDS)
    assert content == b'{"a": 1}'


def test_download_file_returns_none_when_missing():
    session = FakeSession()
    assert download_file("nope.json", session=session, credentials=CREDS) is None


# --- sync_up (best-effort) -------------------------------------------------


def test_sync_up_returns_true_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    local_file = tmp_path / "raw" / "reviews_raw_2026-08-02.json"
    local_file.parent.mkdir(parents=True)
    local_file.write_text("[]")

    session = FakeSession()
    assert sync_up(local_file, session=session, credentials=CREDS) is True


def test_sync_up_returns_false_without_raising_when_store_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    local_file = tmp_path / "raw" / "x.json"
    local_file.parent.mkdir(parents=True)
    local_file.write_text("[]")

    class BrokenSession:
        def post(self, *a, **kw):
            raise ConnectionError("network down")

    assert sync_up(local_file, session=BrokenSession(), credentials=CREDS) is False


# --- sync_down_all (best-effort) -------------------------------------------


def test_sync_down_all_downloads_only_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "tagged").mkdir()
    already_local = tmp_path / "tagged" / "reviews_tagged_2026-08-02.json"
    already_local.write_text('{"already": "here"}')

    session = FakeSession()
    session.store["tagged/reviews_tagged_2026-08-02.json"] = b'{"should": "not overwrite"}'
    session.store["clustered/themes_2026-08-02_90d.json"] = b'{"newly": "downloaded"}'

    count = sync_down_all(session=session, credentials=CREDS)

    assert count == 1  # only the missing one
    assert already_local.read_text() == '{"already": "here"}'  # untouched
    downloaded = tmp_path / "clustered" / "themes_2026-08-02_90d.json"
    assert downloaded.read_bytes() == b'{"newly": "downloaded"}'


def test_sync_down_all_returns_zero_without_raising_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    class BrokenSession:
        def post(self, *a, **kw):
            raise RuntimeError("SUPABASE_URL not found in Streamlit secrets")

    assert sync_down_all(session=BrokenSession(), credentials=CREDS) == 0
