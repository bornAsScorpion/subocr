import stat

from subocr.naming import find_sidecars, satisfies, sidecar_name
from subocr.writer import write_no_clobber

STEM = "Harbor Lights (2011) (1080p BluRay)"


def test_sidecar_name():
    assert sidecar_name(STEM, "en", "full") == f"{STEM}.en.srt"
    assert sidecar_name(STEM, "en", "forced") == f"{STEM}.en.forced.srt"
    assert sidecar_name(STEM, "en", "hi") == f"{STEM}.en.hi.srt"
    assert sidecar_name(STEM, "en", "full", default=True) == f"{STEM}.en.default.srt"


def test_find_and_satisfy(tmp_path):
    video = tmp_path / f"{STEM}.mkv"
    video.touch()
    for name in (f"{STEM}.eng.srt", f"{STEM}.en.forced.srt", f"{STEM}.Russian.hi.srt",
                 f"{STEM}.fr.ass", "Other Movie.en.srt", f"{STEM}.nfo"):
        (tmp_path / name).touch()
    found = {s.path.name: s for s in find_sidecars(video)}
    assert set(found) == {f"{STEM}.eng.srt", f"{STEM}.en.forced.srt",
                          f"{STEM}.Russian.hi.srt", f"{STEM}.fr.ass"}
    subs = list(found.values())
    assert any(satisfies(s, "en", "full") for s in subs)
    assert any(satisfies(s, "en", "forced") for s in subs)
    assert any(satisfies(s, "ru", "full") for s in subs)  # .hi covers full
    assert not any(satisfies(s, "ru", "forced") for s in subs)
    assert not any(satisfies(s, "de", "full") for s in subs)


def test_find_in_extra_dir(tmp_path):
    media, out = tmp_path / "media", tmp_path / "out"
    media.mkdir()
    out.mkdir()
    video = media / "Movie.mkv"
    video.touch()
    (out / "Movie.en.srt").touch()
    assert [s.language for s in find_sidecars(video, [out])] == ["en"]


def test_write_no_clobber(tmp_path):
    dest = tmp_path / "Movie.en.srt"
    assert write_no_clobber(dest, b"first") is True
    assert dest.read_bytes() == b"first"
    assert write_no_clobber(dest, b"second") is False
    assert dest.read_bytes() == b"first"
    assert [p.name for p in tmp_path.iterdir()] == ["Movie.en.srt"]  # no temp left behind
    assert dest.stat().st_mode & stat.S_IROTH or dest.stat().st_mode & stat.S_IRGRP


def test_write_without_chmod_or_hard_links(tmp_path, monkeypatch):
    """CIFS mounts refuse chmod and hard links; the writer must need neither."""
    import os

    def refuse(*a, **k):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "chmod", refuse)
    monkeypatch.setattr(os, "link", refuse)
    dest = tmp_path / "Movie.en.srt"
    assert write_no_clobber(dest, b"subs") is True
    assert dest.read_bytes() == b"subs"
    assert write_no_clobber(dest, b"again") is False  # check-then-rename still won't clobber
    assert [p.name for p in tmp_path.iterdir()] == ["Movie.en.srt"]


def test_write_mode_follows_umask(tmp_path):
    import os

    old = os.umask(0o022)
    try:
        dest = tmp_path / "Movie.en.srt"
        write_no_clobber(dest, b"x")
        assert stat.S_IMODE(dest.stat().st_mode) == 0o644
    finally:
        os.umask(old)


def test_write_failure_is_a_target_failure(tmp_path, monkeypatch):
    from subocr import job
    from subocr.rules import Target
    from tests.conftest import make_info

    def refuse(dest, data):
        raise PermissionError(1, "Operation not permitted", str(dest))

    monkeypatch.setattr(job, "write_no_clobber", refuse)
    track = make_info([{"index": 3, "codec_type": "subtitle", "codec_name": "subrip",
                        "tags": {"language": "eng"}}]).tracks[0]
    w = job._Work(Target("en", "full", track),
                  job.TargetResult("en", "full", 3, "text", job.PLANNED, str(tmp_path / "x.srt")))
    job._write(w, b"data")
    assert w.result.status == job.FAILED
    assert "cannot write sidecar" in w.result.detail
