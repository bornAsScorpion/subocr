"""Read subtitle stream metadata with ffprobe."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .languages import normalize

TEXT_CODECS = frozenset({"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"})
BITMAP_CODECS = frozenset({"hdmv_pgs_subtitle", "dvd_subtitle"})


class ProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Track:
    """One subtitle stream as ffprobe reports it."""

    index: int  # ffprobe stream index (what `ffmpeg -map 0:N` uses)
    ordinal: int  # position among subtitle streams, 0-based (maps to mkvmerge track order)
    codec: str
    language: str | None  # ISO 639-1; None when missing or undetermined
    raw_language: str | None = None
    title: str = ""
    forced: bool = False
    default: bool = False
    hearing_impaired: bool = False
    comment: bool = False
    frames: int | None = None  # packet count from mkvmerge statistics tags, when present

    @property
    def cues(self) -> int | None:
        """Estimated cue count.

        PGS stores two packets per cue (one shows it, one clears it), so its packet
        count is about twice the cue count; VobSub and text tracks are one per cue.
        """
        if self.frames is None:
            return None
        return self.frames // 2 if self.codec == "hdmv_pgs_subtitle" else self.frames

    @property
    def kind(self) -> str:
        if self.codec in TEXT_CODECS:
            return "text"
        if self.codec in BITMAP_CODECS:
            return "bitmap"
        return "other"


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    container: str
    duration: float | None  # seconds
    tracks: tuple[Track, ...]

    @property
    def is_matroska(self) -> bool:
        return "matroska" in self.container


def _frames(tags: dict[str, str]) -> int | None:
    for key, value in tags.items():
        if key == "number_of_frames" or key.startswith("number_of_frames-"):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def parse_ffprobe(data: dict, path: Path) -> MediaInfo:
    """Build MediaInfo from `ffprobe -print_format json -show_format -show_streams` output."""
    fmt = data.get("format") or {}
    try:
        duration: float | None = float(fmt["duration"])
    except (KeyError, TypeError, ValueError):
        duration = None

    subs = [s for s in data.get("streams") or [] if s.get("codec_type") == "subtitle"]
    tracks = []
    for ordinal, s in enumerate(subs):
        tags = {k.lower(): v for k, v in (s.get("tags") or {}).items()}
        disp = s.get("disposition") or {}
        raw_lang = tags.get("language")
        tracks.append(
            Track(
                index=int(s["index"]),
                ordinal=ordinal,
                codec=s.get("codec_name") or "unknown",
                language=normalize(raw_lang),
                raw_language=raw_lang,
                title=tags.get("title") or "",
                forced=bool(disp.get("forced")),
                default=bool(disp.get("default")),
                hearing_impaired=bool(disp.get("hearing_impaired")),
                comment=bool(disp.get("comment")),
                frames=_frames(tags),
            )
        )
    return MediaInfo(
        path=path,
        container=fmt.get("format_name") or "",
        duration=duration,
        tracks=tuple(tracks),
    )


def probe_media(path: Path) -> MediaInfo:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", "-select_streams", "s", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise ProbeError("ffprobe not found on PATH") from exc
    if proc.returncode != 0:
        raise ProbeError(f"ffprobe failed: {proc.stderr.strip()[-500:]}")
    return parse_ffprobe(json.loads(proc.stdout or "{}"), path)
