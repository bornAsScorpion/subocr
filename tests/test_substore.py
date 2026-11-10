"""Archive, delete and restore against a real temp library and archive."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from subocr.config import Config, ConfigError
from subocr.service.settings import ServiceSettings
from subocr.service.store import Store
from subocr.service.substore import SubtitleStore


@pytest.fixture
def env(tmp_path):
    lib = tmp_path / "data" / "Movies"
    folder = lib / "Film (2000)"
    folder.mkdir(parents=True)
    video = folder / "Film (2000).mkv"
    video.write_bytes(b"v" * 100)
    for name, body in {"Film (2000).en.srt": "EN", "Film (2000).da.srt": "DA",
                       "Film (2000).de.forced.srt": "DE-F", "Film (2000).iTunes.srt": "??",
                       "Film (2000).fr.idx": "IDX", "Film (2000).fr.sub": "SUB"}.items():
        (folder / name).write_text(body)
    settings = ServiceSettings(library_paths=(str(lib),), api_key="k",
                               archive_root=str(tmp_path / "archive"), cleanup_mode="archive")
    store = Store(":memory:")
    return SubtitleStore(store, settings), store, settings, video, tmp_path


def _names(folder):
    return sorted(p.name for p in folder.iterdir())


def test_dry_run_changes_nothing(env):
    subs, store, settings, video, tmp = env
    subs.settings = replace(settings, cleanup_mode="dry-run")
    before = _names(video.parent)
    report = subs.cleanup(video, ("en",))
    assert _names(video.parent) == before
    assert len(report.would_archive) == 4 and not report.changed
    assert report.kept_unknown == [str(video.parent / "Film (2000).iTunes.srt")]
    assert len(store.recent_events(event="cleanup-would-archive")) == 4
    assert not (tmp / "archive").exists()


def test_off_does_nothing(env):
    subs, store, settings, video, _ = env
    subs.settings = replace(settings, cleanup_mode="off")
    assert subs.cleanup(video, ("en",)).to_dict() == {"mode": "off"}


def test_archive_moves_and_indexes(env):
    subs, store, settings, video, tmp = env
    report = subs.cleanup(video, ("en",))
    assert sorted(os.path.basename(p) for p in report.archived) == [
        "Film (2000).da.srt", "Film (2000).de.forced.srt", "Film (2000).fr.idx",
        "Film (2000).fr.sub"]
    assert _names(video.parent) == ["Film (2000).en.srt", "Film (2000).iTunes.srt",
                                    "Film (2000).mkv"]
    archived_dir = tmp / "archive" / "Movies" / "Film (2000)"
    assert (archived_dir / "Film (2000).da.srt").read_text() == "DA"
    rows = store.list_archive()
    assert len(rows) == 4 and {r["status"] for r in rows} == {"archived"}
    assert {r["suffix"] for r in rows} == {".da.srt", ".de.forced.srt", ".fr.idx", ".fr.sub"}
    assert subs.cleanup(video, ("en",)).archived == []  # idempotent


def test_archive_name_collision(env):
    subs, store, settings, video, tmp = env
    archived_dir = tmp / "archive" / "Movies" / "Film (2000)"
    archived_dir.mkdir(parents=True)
    (archived_dir / "Film (2000).da.srt").write_text("older copy")
    subs.cleanup(video, ("en",))
    assert (archived_dir / "Film (2000).da.srt").read_text() == "older copy"
    assert (archived_dir / "Film (2000).da (2).srt").read_text() == "DA"


def test_delete_mode(env):
    subs, store, settings, video, tmp = env
    subs.settings = replace(settings, cleanup_mode="delete")
    report = subs.cleanup(video, ("en",))
    assert len(report.deleted) == 4
    assert not (tmp / "archive").exists()
    assert {r["status"] for r in store.list_archive()} == {"deleted"}
    assert (video.parent / "Film (2000).iTunes.srt").exists()  # unknown never deleted


def test_restore_when_language_added(env):
    subs, store, settings, video, tmp = env
    subs.cleanup(video, ("en",))
    results = subs.restore_wanted(video, ("en", "de", "fr"))
    assert sorted(r["status"] for r in results) == ["restored"] * 3  # de forced, fr idx+sub
    assert (video.parent / "Film (2000).de.forced.srt").read_text() == "DE-F"
    assert (video.parent / "Film (2000).fr.idx").exists()
    assert (video.parent / "Film (2000).fr.sub").exists()
    assert not (tmp / "archive" / "Movies" / "Film (2000)" / "Film (2000).de.forced.srt").exists()
    assert subs.restore_wanted(video, ("en", "de", "fr")) == []  # nothing left to restore
    # da stays archived: not wanted
    assert [r["language"] for r in store.list_archive(status="archived")] == ["da"]


def test_restore_never_clobbers(env):
    subs, store, settings, video, _ = env
    subs.cleanup(video, ("en",))
    (video.parent / "Film (2000).da.srt").write_text("from Bazarr")
    assert subs.restore_wanted(video, ("da",)) == []  # covered already
    entry = store.list_archive(status="archived")[0]
    da = next(r for r in store.list_archive() if r["language"] == "da")
    result = subs.restore_entry(da, video)
    assert result["status"] == "skipped"
    assert (video.parent / "Film (2000).da.srt").read_text() == "from Bazarr"
    assert entry["status"] == "archived"


def test_restore_after_release_upgrade_is_unverified(env):
    subs, store, settings, video, _ = env
    subs.cleanup(video, ("en",))
    video.unlink()  # Radarr replaced the release
    new = video.parent / "Film (2000) Remux.mkv"
    new.write_bytes(b"w" * 500)
    results = subs.restore_wanted(new, ("da",))
    assert [r["status"] for r in results] == ["restored-unverified"]
    assert (new.parent / "Film (2000) Remux.da.srt").read_text() == "DA"


def test_archive_root_must_be_outside_libraries(tmp_path):
    with pytest.raises(ConfigError):
        ServiceSettings(library_paths=("/data/Movies",), archive_root="/data/Movies/.archive")
    with pytest.raises(ConfigError):
        ServiceSettings(library_paths=("/data/Movies",), archive_root="/data")


def test_job_restores_and_cleanup_keeps_global_plus_job_languages(env, monkeypatch):
    """A job for ["da"] restores Danish, keeps it, and never archives the English sub."""
    from subocr.service import jobs as jobs_mod
    from subocr.service.jobs import JobService
    from tests.test_service_jobs import FakeNotifier

    subs, store, settings, video, _ = env
    monkeypatch.setattr(jobs_mod.ocr, "installed_models", lambda: frozenset({"eng"}))
    svc = JobService(store, Config(languages=("en",), text_mode="container"), settings,
                     FakeNotifier())
    svc.subs.cleanup(video, ("en",))  # da, de, fr archived
    seen = []
    monkeypatch.setattr(jobs_mod.core, "process_file", lambda path, cfg, **kw: seen.append(
        sorted(p.name for p in path.parent.iterdir())) or jobs_mod.core.JobResult(
        str(path), "complete", []))
    svc.submit(str(video), "api", languages=["da"])
    assert svc.run_once()
    assert "Film (2000).da.srt" in seen[0]  # restored before process_file ran
    job = dict(store.list_jobs()[0])
    assert job["outcome"] == "restored"
    # cleanup kept LANGUAGES (en) plus the job's da; de and fr stay archived
    assert (video.parent / "Film (2000).en.srt").exists()
    assert (video.parent / "Film (2000).da.srt").exists()
    assert sorted(r["language"] for r in store.list_archive(status="archived")) == [
        "de", "fr", "fr"]
    assert svc.notifier.updated == [str(video)]


def test_orphans_deleted_after_upgrade(env):
    subs, store, settings, video, tmp = env
    old = video.parent / "Old Release (2000).fr.srt"
    old.write_text("old fr")
    (video.parent / "Old Release (2000).en.srt").write_text("old en")
    report = subs.cleanup(video, ("en",))
    assert not old.exists() and not (video.parent / "Old Release (2000).en.srt").exists()
    assert sorted(os.path.basename(p) for p in report.deleted) == [
        "Old Release (2000).en.srt", "Old Release (2000).fr.srt"]
    rows = [r for r in store.list_archive() if r["status"] == "deleted"]
    assert {r["suffix"] for r in rows} == {".en.srt", ".fr.srt"}


def test_orphans_archived_restore_unverified(env):
    subs, store, settings, video, tmp = env
    subs.settings = replace(settings, orphan_subs="archive")
    (video.parent / "Old Release (2000).pl.srt").write_text("old pl")
    subs.cleanup(video, ("en",))
    assert (tmp / "archive" / "Movies" / "Film (2000)" / "Old Release (2000).pl.srt").exists()
    results = subs.restore_wanted(video, ("en", "pl"))
    assert [r["status"] for r in results] == ["restored-unverified"]
    assert (video.parent / "Film (2000).pl.srt").read_text() == "old pl"


def test_pending_checks(env):
    subs, store, settings, video, _ = env
    assert subs.cleanup_pending(video, ("en",))  # da, de, fr waiting
    subs.cleanup(video, ("en",))
    assert not subs.cleanup_pending(video, ("en",))
    (video.parent / "Film (2000).it.srt").write_text("added later by Bazarr")
    assert subs.cleanup_pending(video, ("en",))
    assert subs.restore_pending(video, ("en", "da"))
    assert not subs.restore_pending(video, ("en",))
    subs.settings = replace(settings, cleanup_mode="dry-run")
    assert not subs.cleanup_pending(video, ("en",))  # dry-run never re-queues


def test_failure_names_the_step(env, monkeypatch):
    from pathlib import Path

    subs, store, settings, video, tmp = env
    real_unlink = Path.unlink

    def deny(self, *a, **k):
        if self.name.endswith(".da.srt") and self.parent == video.parent:
            raise PermissionError(13, "Permission denied", str(self))
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", deny)
    report = subs.cleanup(video, ("en",))
    [failure] = report.failed
    assert "remove-source" in failure["error"]
    assert (video.parent / "Film (2000).da.srt").exists()  # original kept
    assert not (tmp / "archive" / "Movies" / "Film (2000)" / "Film (2000).da.srt").exists()
