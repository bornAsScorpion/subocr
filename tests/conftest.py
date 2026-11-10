"""Fixtures modelled on real-world release layouts."""

from __future__ import annotations

from pathlib import Path

import pytest

from subocr.probe import MediaInfo, parse_ffprobe


def _stream(index, codec, lang=None, title=None, forced=0, hi=0, comment=0, frames=None):
    tags = {}
    if lang:
        tags["language"] = lang
    if title:
        tags["title"] = title
    if frames is not None:
        tags[f"NUMBER_OF_FRAMES-{lang or 'und'}"] = str(frames)
    return {
        "index": index,
        "codec_type": "subtitle",
        "codec_name": codec,
        "tags": tags,
        "disposition": {"default": 0, "forced": forced, "hearing_impaired": hi,
                        "comment": comment},
    }


def make_info(streams, duration_min=100.0, name="Movie (2000).mkv") -> MediaInfo:
    data = {
        "format": {"format_name": "matroska,webm", "duration": str(duration_min * 60)},
        "streams": streams,
    }
    return parse_ffprobe(data, Path("/media/Movies") / name)


@pytest.fixture
def harbor() -> MediaInfo:
    """VobSub release: three English tracks: full, forced, commentary."""
    return make_info(
        [
            _stream(3, "dvd_subtitle", "eng", frames=1420),
            _stream(4, "dvd_subtitle", "eng", title="Forced", forced=1, frames=7),
            _stream(5, "dvd_subtitle", "eng", title="Commentary", frames=1835),
        ],
        duration_min=96,
        name="Harbor Lights (2011) (1080p BluRay).mkv",
    )


@pytest.fixture
def courier() -> MediaInfo:
    """PGS + SRT twins per language; the English PGS is wrongly flagged forced."""
    return make_info(
        [
            _stream(3, "hdmv_pgs_subtitle", "eng", title="PGS", forced=1, frames=2270),
            _stream(4, "subrip", "eng", title="SRT", frames=1135),
            _stream(5, "hdmv_pgs_subtitle", "ger", title="PGS", frames=1968),
            _stream(6, "subrip", "ger", title="SRT", frames=982),
            _stream(7, "hdmv_pgs_subtitle", "hun", title="PGS", frames=2224),
            _stream(8, "subrip", "hun", title="SRT", frames=1112),
        ],
        duration_min=108,
        name="Night Courier (1981) (1080p BluRay).mkv",
    )


@pytest.fixture
def stream():
    return _stream
