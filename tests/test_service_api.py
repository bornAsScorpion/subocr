"""HTTP API through FastAPI's TestClient."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from subocr.config import Config
from subocr.service import jobs as jobs_mod
from subocr.service.api import create_app
from subocr.service.runner import build_services
from subocr.service.settings import ServiceSettings

KEY = "test-key"


@pytest.fixture(autouse=True)
def no_tesseract(monkeypatch):
    monkeypatch.setattr(jobs_mod.ocr, "installed_models", lambda: frozenset({"eng"}))


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    movie = data / "Movies" / "Harbor Lights (2011)"
    movie.mkdir(parents=True)
    (data / "Shows").mkdir()
    video = movie / "Harbor Lights (2011).mkv"
    video.write_bytes(b"x")
    settings = ServiceSettings(
        library_paths=(str(data / "Movies"), str(data / "Shows")),
        path_mappings=(("/mnt/media", str(data)),),
        api_key=KEY,
        config_dir=str(tmp_path / "config"),
        reconcile_at=None,
    )
    svc = build_services(settings, Config())
    client = TestClient(create_app(svc))
    client.headers["X-Api-Key"] = KEY
    return client, svc, data, video


def test_health_needs_no_key(env):
    client, *_ = env
    assert TestClient(client.app).get("/health").json() == {"status": "ok"}


def test_auth_header_and_query(env):
    client, *_ = env
    bare = TestClient(client.app)
    assert bare.get("/api/v1/system/status").status_code == 401
    assert bare.get("/api/v1/system/status", headers={"X-Api-Key": "nope"}).status_code == 401
    assert bare.get(f"/api/v1/system/status?apikey={KEY}").status_code == 200
    status = client.get("/api/v1/system/status").json()
    assert status["ocr_models"] == ["eng"] and status["languages"] == ["en"]


def test_submit_job_with_jellyfin_path(env):
    client, svc, data, video = env
    r = client.post("/api/v1/jobs", json={"path": "/mnt/media/Movies/Harbor Lights (2011)/"
                                                  "Harbor Lights (2011).mkv", "source": "plugin"})
    assert r.status_code == 202
    body = r.json()
    assert body["path"] == str(video)
    assert len(body["job_ids"]) == 2  # auto mode: OCR now, text later
    job = client.get(f"/api/v1/jobs/{body['id']}").json()
    assert (job["source"], job["status"]) == ("plugin", "queued")
    again = client.post("/api/v1/jobs", json={"path": str(video)}).json()
    assert again["deduplicated"] is True


def test_submit_job_ocr_only_and_languages(env):
    client, _, _, video = env
    body = client.post("/api/v1/jobs", json={"path": str(video), "scope": "ocr-only",
                                             "languages": ["eng", "Russian"]}).json()
    assert len(body["job_ids"]) == 1
    job = client.get(f"/api/v1/jobs/{body['id']}").json()
    assert (job["text_mode"], job["languages"]) == ("plugin", "en,ru")


@pytest.mark.parametrize(("payload", "code"), [
    ({"path": "/etc/passwd"}, 400),
    ({"path": "/mnt/media/Movies/../../../etc/passwd"}, 400),
    ({"path": "x", "scope": "everything"}, 422),
])
def test_submit_job_rejections(env, payload, code):
    client, *_ = env
    assert client.post("/api/v1/jobs", json=payload).status_code == code


def test_submit_job_unknown_language(env):
    client, _, _, video = env
    r = client.post("/api/v1/jobs", json={"path": str(video), "languages": ["klingon"]})
    assert r.status_code == 422


def test_queue_full_returns_429(env, monkeypatch):
    client, svc, _, video = env

    def full(*a, **k):
        raise jobs_mod.QueueFull("full")

    monkeypatch.setattr(svc.jobs, "submit", full)
    r = client.post("/api/v1/jobs", json={"path": str(video)})
    assert r.status_code == 429 and r.headers["Retry-After"] == "60"


def test_unknown_job_404(env):
    client, *_ = env
    assert client.get("/api/v1/jobs/999").status_code == 404


def test_radarr_webhooks(env):
    client, svc, data, video = env
    assert client.post("/api/v1/webhooks/radarr", json={"eventType": "Test"}).json()["status"] \
        == "ok"
    payload = {"eventType": "Download", "movie": {"folderPath": str(video.parent)},
               "movieFile": {"relativePath": video.name}}
    r = client.post(f"/api/v1/webhooks/radarr?apikey={KEY}", json=payload).json()
    assert r["status"] == "accepted" and r["paths"] == [str(video)] and len(r["job_ids"]) == 2
    r = client.post("/api/v1/webhooks/radarr", json={
        "eventType": "MovieFileDelete", "movieFile": {"path": str(video)}}).json()
    assert r["status"] == "accepted"


def test_webhook_outside_library_ignored(env):
    client, *_ = env
    r = client.post("/api/v1/webhooks/sonarr", json={
        "eventType": "Download", "series": {"path": "/elsewhere/Show"},
        "episodeFile": {"relativePath": "a.mkv"}}).json()
    assert r["status"] == "ignored" and r["rejected"]


def test_rename_carries_state(env):
    client, svc, data, video = env
    svc.store.upsert_media(str(video.parent / "old.mkv"), 1, 1.0, "fp", "complete")
    client.post("/api/v1/webhooks/radarr", json={"eventType": "Rename", "renamedMovieFiles": [
        {"path": str(video), "previousPath": str(video.parent / "old.mkv")}]})
    assert svc.store.get_media(str(video))["outcome"] == "complete"
    assert svc.store.get_media(str(video.parent / "old.mkv")) is None


def test_jellyfin_webhook_with_path(env):
    client, _, _, video = env
    r = client.post("/api/v1/webhooks/jellyfin", json={
        "NotificationType": "ItemAdded", "ItemType": "Movie",
        "Path": "/mnt/media/Movies/Harbor Lights (2011)/Harbor Lights (2011).mkv"}).json()
    assert r["paths"] == [str(video)]


def test_jellyfin_webhook_item_id_lookup(env, monkeypatch):
    client, svc, _, video = env
    monkeypatch.setattr(svc.notifier, "lookup_path",
                        lambda item_id: "/mnt/media/Movies/Harbor Lights (2011)/"
                                        "Harbor Lights (2011).mkv")
    r = client.post("/api/v1/webhooks/jellyfin", json={
        "NotificationType": "ItemAdded", "ItemType": "Movie", "ItemId": "abc"}).json()
    assert r["paths"] == [str(video)]


def test_reconcile_endpoint(env):
    client, svc, data, _ = env
    r = client.post("/api/v1/reconcile", json={"paths": ["/mnt/media/Movies"]})
    assert r.status_code == 202 and r.json()["paths"] == [str(data / "Movies")]
    assert client.post("/api/v1/reconcile", json={"paths": ["/etc"]}).status_code == 400


def test_openapi_published(env):
    client, *_ = env
    spec = client.get("/openapi.json").json()
    assert "/api/v1/jobs" in spec["paths"]


@pytest.mark.parametrize("hook", ["radarr", "sonarr", "jellyfin"])
def test_webhook_empty_or_bad_body_is_400(env, hook):
    client, *_ = env
    assert client.post(f"/api/v1/webhooks/{hook}").status_code == 400
    r = client.post(f"/api/v1/webhooks/{hook}", content=b"not json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert client.post(f"/api/v1/webhooks/{hook}", json=[1, 2]).status_code == 400


def test_jellyfin_webhook_form_encoded(env):
    client, _, _, video = env
    r = client.post("/api/v1/webhooks/jellyfin", data={
        "NotificationType": "ItemAdded", "ItemType": "Movie",
        "Path": "/mnt/media/Movies/Harbor Lights (2011)/Harbor Lights (2011).mkv"})
    assert r.status_code == 200 and r.json()["paths"] == [str(video)]


def test_webhook_docs_show_body(env):
    client, *_ = env
    spec = client.get("/openapi.json").json()
    body = spec["paths"]["/api/v1/webhooks/jellyfin"]["post"]["requestBody"]
    assert body["content"]["application/json"]["example"]["NotificationType"] == "ItemAdded"


def test_missing_file_refused_up_front(env):
    client, svc, _, video = env
    missing = "/mnt/media/Movies/Harbor Lights (2011)/Harbor Lights (2011) (1080p).mkv"
    r = client.post("/api/v1/jobs", json={"path": missing})
    assert r.status_code == 404 and "file not found" in r.json()["detail"]
    r = client.post("/api/v1/webhooks/jellyfin", json={
        "NotificationType": "ItemAdded", "ItemType": "Movie", "Path": missing}).json()
    assert r["status"] == "ignored" and "file not found" in r["rejected"][0]["reason"]
    assert svc.store.list_jobs() == []


def test_cleanup_preview_and_archive_endpoints(env):
    client, svc, data, video = env
    for lang in ("en", "da", "fr"):
        (video.parent / f"{video.stem}.{lang}.srt").write_text(lang)
    r = client.post("/api/v1/cleanup/preview",
                    json={"path": "/mnt/media/Movies/Harbor Lights (2011)"}).json()
    assert r["mode"] == "dry-run" and r["applies"] is False
    assert r["totals"] == {"keep": 1, "archive": 2, "delete": 0}
    assert client.post("/api/v1/cleanup/preview", json={"path": "/etc"}).status_code == 400
    assert client.get("/api/v1/archive").json() == []
    assert client.post("/api/v1/archive/1/restore").status_code == 404


def test_status_shows_cleanup(env):
    client, *_ = env
    status = client.get("/api/v1/system/status").json()
    assert status["cleanup"]["mode"] == "dry-run"


def test_events_filter(env):
    client, svc, *_ = env
    svc.store.add_event("/x", "archived", "a")
    svc.store.add_event("/x", "other", "b")
    assert [e["event"] for e in client.get("/api/v1/events?event=archived").json()] == [
        "archived"]


def test_reconcile_force_flag(env):
    client, *_ = env
    r = client.post("/api/v1/reconcile", json={"paths": ["/mnt/media/Movies"], "force": True})
    assert r.status_code == 202 and r.json()["force"] is True


def test_preview_lists_orphans_once_per_folder(env):
    client, svc, data, video = env
    (video.parent / "Episode 2.mkv").write_bytes(b"x")
    (video.parent / "Old.Release.it.srt").write_text("old")
    r = client.post("/api/v1/cleanup/preview",
                    json={"path": "/mnt/media/Movies/Harbor Lights (2011)"}).json()
    orphans = [sc for item in r["items"] for sc in item["sidecars"] if sc["orphan"]]
    assert len(orphans) == 1 and orphans[0]["action"] == "delete"
