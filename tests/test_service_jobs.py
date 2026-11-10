"""Queue, auto-mode split, worker execution and the reconcile scan (no external tools)."""

from __future__ import annotations

import os
import time
from dataclasses import replace

import pytest

from subocr import job as core
from subocr.config import Config
from subocr.service import jobs as jobs_mod
from subocr.service.jobs import DO_TEXT, HIGH, LOW, SKIP_TEXT, JobService, QueueFull
from subocr.service.notify import Notifier
from subocr.service.paths import PathPolicy
from subocr.service.reconcile import Reconciler
from subocr.service.settings import ServiceSettings
from subocr.service.store import Store


class FakeNotifier(Notifier):
    def __init__(self):
        self.updated: list[str] = []
        self.callbacks: list[tuple[str, dict]] = []

    def media_updated(self, local_path):
        self.updated.append(local_path)

    def callback(self, url, payload):
        self.callbacks.append((url, payload))


@pytest.fixture(autouse=True)
def no_tesseract(monkeypatch):
    monkeypatch.setattr(jobs_mod.ocr, "installed_models", lambda: frozenset({"eng"}))


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "Movies"
    (root / "A (2000)").mkdir(parents=True)
    (root / "B (2001)").mkdir()
    (root / "A (2000)" / "A (2000).mkv").write_bytes(b"x" * 10)
    (root / "B (2001)" / "B (2001).mp4").write_bytes(b"y" * 10)
    (root / "B (2001)" / "B (2001).nfo").write_text("not a video")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "skip.mkv").write_bytes(b"z")
    return root


@pytest.fixture
def make_service(tmp_path, lib):
    def _make(cfg: Config | None = None, **settings_kw):
        settings = ServiceSettings(library_paths=(str(lib),), api_key="k",
                                   config_dir=str(tmp_path / "config"), **settings_kw)
        store = Store(":memory:")
        notifier = FakeNotifier()
        svc = JobService(store, cfg or Config(), settings, notifier)
        return svc, store, notifier, settings
    return _make


def _jobs(store):
    return [dict(r) for r in reversed(store.list_jobs(limit=100))]


def test_auto_mode_splits_ocr_now_text_later(make_service, lib):
    svc, store, _, _ = make_service(plugin_grace_minutes=10)
    before = time.time()
    sub = svc.submit(str(lib / "A (2000)" / "A (2000).mkv"), "radarr")
    jobs = _jobs(store)
    assert len(sub.job_ids) == 2 and not sub.deduplicated
    assert [j["text_mode"] for j in jobs] == [SKIP_TEXT, DO_TEXT]
    assert jobs[0]["not_before"] <= time.time()
    assert jobs[1]["not_before"] >= before + 600
    assert all(j["priority"] == HIGH for j in jobs)


def test_reconcile_source_does_everything_now(make_service, lib):
    svc, store, _, _ = make_service()
    svc.submit(str(lib / "A (2000)" / "A (2000).mkv"), "reconcile", low_priority=True)
    [job] = _jobs(store)
    assert (job["text_mode"], job["priority"]) == (DO_TEXT, LOW)


@pytest.mark.parametrize(("mode", "scope", "expected"), [
    ("container", "full", [DO_TEXT]),
    ("plugin", "full", [SKIP_TEXT]),
    ("auto", "ocr-only", [SKIP_TEXT]),
    ("container", "ocr-only", [SKIP_TEXT]),
])
def test_modes_and_scope(make_service, lib, mode, scope, expected):
    svc, store, _, _ = make_service(Config(text_mode=mode))
    svc.submit(str(lib / "A (2000)" / "A (2000).mkv"), "api", scope=scope)
    assert [j["text_mode"] for j in _jobs(store)] == expected


def test_dedup_and_callback_on_last_job(make_service, lib):
    svc, store, _, _ = make_service()
    path = str(lib / "A (2000)" / "A (2000).mkv")
    first = svc.submit(path, "jellyfin", callback_url="http://cb")
    again = svc.submit(path, "radarr")
    assert again.deduplicated and again.job_ids == first.job_ids
    jobs = _jobs(store)
    assert len(jobs) == 2
    assert [j["callback_url"] for j in jobs] == [None, "http://cb"]
    # a different language set is a different job
    assert not svc.submit(path, "api", languages=["ru"]).deduplicated


def test_queue_limit_applies_to_high_priority_only(make_service, lib):
    svc, _, _, _ = make_service(Config(text_mode="container"), job_queue_limit=1)
    svc.submit(str(lib / "A (2000)" / "A (2000).mkv"), "api")
    with pytest.raises(QueueFull):
        svc.submit(str(lib / "B (2001)" / "B (2001).mp4"), "api")
    svc.submit(str(lib / "B (2001)" / "B (2001).mp4"), "reconcile", low_priority=True)


def test_worker_priority_and_due_time(make_service, lib):
    svc, store, _, _ = make_service(Config(text_mode="container"))
    store.add_job("/low", "reconcile", DO_TEXT, None, LOW, time.time() - 5)
    store.add_job("/later", "api", DO_TEXT, None, HIGH, time.time() + 3600)
    store.add_job("/high", "api", DO_TEXT, None, HIGH, time.time())
    assert store.next_job()["path"] == "/high"
    assert store.next_job()["path"] == "/low"
    assert store.next_job() is None  # "/later" is not due yet


def test_execute_records_state_and_notifies(make_service, lib, monkeypatch, tmp_path):
    svc, store, notifier, _ = make_service(Config(text_mode="container"))
    video = lib / "A (2000)" / "A (2000).mkv"
    sidecar = lib / "A (2000)" / "A (2000).en.srt"
    seen_cfg = {}

    def fake_process(path, cfg, **kw):
        seen_cfg["cfg"] = cfg
        sidecar.write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        r = core.TargetResult("en", "full", 3, "bitmap", core.WRITTEN, str(sidecar), 0.99)
        return core.JobResult(str(path), "ocr-done", [r])

    monkeypatch.setattr(jobs_mod.core, "process_file", fake_process)
    svc.submit(str(video), "api", callback_url="http://cb")
    assert svc.run_once()
    job = _jobs(store)[0]
    assert (job["status"], job["outcome"]) == ("done", "ocr-done")
    assert seen_cfg["cfg"].text_mode == DO_TEXT
    media = store.get_media(str(video))
    assert media["outcome"] == "ocr-done" and media["fingerprint"] == svc.fingerprint
    [out] = store.outputs_for(str(video))
    assert (out["source"], out["status"], out["quality"]) == ("ocr", "ocr", 0.99)
    assert out["content_hash"]
    assert notifier.updated == [str(video)]
    assert notifier.callbacks[0][0] == "http://cb"
    assert notifier.callbacks[0][1]["outcome"] == "ocr-done"


def test_execute_failure_does_not_stop_worker(make_service, lib, monkeypatch):
    svc, store, notifier, _ = make_service(Config(text_mode="container"))

    def boom(path, cfg, **kw):
        raise RuntimeError("ffprobe exploded")

    monkeypatch.setattr(jobs_mod.core, "process_file", boom)
    svc.submit(str(lib / "A (2000)" / "A (2000).mkv"), "api")
    assert svc.run_once()
    job = _jobs(store)[0]
    assert (job["status"], job["outcome"]) == ("failed", "failed")
    assert "exploded" in job["error"]


def test_execute_missing_file(make_service, lib):
    svc, store, _, _ = make_service(Config(text_mode="container"))
    svc.submit(str(lib / "gone.mkv"), "api")
    assert svc.run_once()
    assert _jobs(store)[0]["outcome"] == "missing"


def test_requeue_running():
    store = Store(":memory:")
    store.add_job("/a", "api", DO_TEXT, None, HIGH, 0)
    assert store.next_job()["status"] == "running"
    assert store.requeue_running() == 1
    assert store.next_job()["path"] == "/a"


# --- reconcile -----------------------------------------------------------------


def _reconciler(make_service, cfg=None):
    svc, store, _, settings = make_service(cfg or Config())
    return Reconciler(store, svc, settings), svc, store


def test_reconcile_queues_new_then_skips_unchanged(make_service, lib):
    rec, svc, store = _reconciler(make_service)
    result = rec.scan()
    assert (result["seen"], result["queued"]) == (2, 2)  # .nfo and .hidden ignored
    jobs = _jobs(store)
    assert {j["source"] for j in jobs} == {"reconcile"}
    assert {j["text_mode"] for j in jobs} == {DO_TEXT}

    for j in jobs:  # pretend the worker processed them
        st = os.stat(j["path"])
        store.upsert_media(j["path"], st.st_size, st.st_mtime, svc.fingerprint, "complete")
        store.finish_job(j["id"], "complete", None, None)
    result = rec.scan()
    assert (result["queued"], result["unchanged"]) == (0, 2)


def test_reconcile_requeues_changed_failed_and_new_settings(make_service, lib):
    rec, svc, store = _reconciler(make_service)
    a = str(lib / "A (2000)" / "A (2000).mkv")
    b = str(lib / "B (2001)" / "B (2001).mp4")
    st_a, st_b = os.stat(a), os.stat(b)
    store.upsert_media(a, st_a.st_size + 1, st_a.st_mtime, svc.fingerprint, "complete")  # changed
    store.upsert_media(b, st_b.st_size, st_b.st_mtime, svc.fingerprint, "failed")  # retry
    assert rec.scan()["queued"] == 2

    rec2, svc2, store2 = _reconciler(make_service, Config(languages=("en", "ru")))
    for p, st in ((a, st_a), (b, st_b)):
        store2.upsert_media(p, st.st_size, st.st_mtime, "old-fingerprint", "complete")
    assert rec2.scan()["queued"] == 2  # settings changed since the last run


def test_reconcile_marks_gone(make_service, lib):
    rec, svc, store = _reconciler(make_service)
    ghost = str(lib / "Gone (1999)" / "Gone (1999).mkv")
    store.upsert_media(ghost, 1, 1.0, svc.fingerprint, "complete")
    assert rec.scan()["gone"] == 1
    assert store.get_media(ghost)["gone"] == 1


def test_reconcile_single_flight(make_service, lib):
    rec, _, _ = _reconciler(make_service)
    rec._running.acquire()
    try:
        assert rec.scan() == {"status": "already-running"}
        assert rec.scan_in_background() is False
    finally:
        rec._running.release()


def test_fingerprint_tracks_models_and_settings():
    base = jobs_mod.config_fingerprint(Config(), ["eng"])
    assert jobs_mod.config_fingerprint(Config(), ["eng"]) == base
    assert jobs_mod.config_fingerprint(Config(), ["eng", "rus"]) != base
    assert jobs_mod.config_fingerprint(replace(Config(), hi_policy="both"), ["eng"]) != base
    # text_mode differs per job, so it must not change the fingerprint
    assert jobs_mod.config_fingerprint(replace(Config(), text_mode="plugin"), ["eng"]) == base


def test_policy_used_by_service_is_independent():
    assert PathPolicy(("/data/Movies",)).resolve("/data/Movies/x.mkv") == "/data/Movies/x.mkv"


def test_reconcile_requeues_pending_cleanup_and_force(make_service, lib):
    from dataclasses import replace as dc_replace

    svc, store, _, settings = make_service()
    settings = dc_replace(settings, cleanup_mode="archive", archive_root=str(lib.parent / "arch"))
    svc.settings = settings
    svc.subs.settings = settings
    svc.fingerprint = jobs_mod.config_fingerprint(svc.cfg, ["eng"], settings)
    rec = Reconciler(store, svc, settings)
    for p in (lib / "A (2000)" / "A (2000).mkv", lib / "B (2001)" / "B (2001).mp4"):
        st = os.stat(p)
        store.upsert_media(str(p), st.st_size, st.st_mtime, svc.fingerprint, "complete")
    assert rec.scan()["queued"] == 0
    (lib / "A (2000)" / "A (2000).fr.srt").write_text("left behind")  # unwanted, video unchanged
    result = rec.scan()
    assert result["queued"] == 1 and result["reasons"] == {"cleanup": 1}
    result = rec.scan(force=True)
    assert result["queued"] == 2 and result["reasons"] == {"forced": 2}
