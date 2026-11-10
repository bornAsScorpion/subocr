"""Sidecar filenames: what we write, and what already exists.

Names follow Jellyfin and Bazarr conventions: `Movie.en.srt`, `Movie.en.forced.srt`,
`Movie.en.hi.srt`, optionally `Movie.en.default.srt`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .languages import normalize

SUB_EXTS = frozenset({".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup"})
VIDEO_EXTS = frozenset({".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".webm"})
_FORCED_TOKENS = frozenset({"forced", "foreign"})
# Tokens that are flags, never languages ("hi" would otherwise read as Hindi)
FLAG_TOKENS = frozenset({"forced", "foreign", "hi", "sdh", "cc", "default", "sub", "subs"})


@dataclass(frozen=True)
class Sidecar:
    path: Path
    language: str | None
    flags: frozenset[str]

    @property
    def forced(self) -> bool:
        return bool(self.flags & _FORCED_TOKENS)


def sidecar_name(video_stem: str, language: str, variant: str, default: bool = False) -> str:
    parts = [video_stem, language]
    if default:
        parts.append("default")
    if variant == "forced":
        parts.append("forced")
    elif variant == "hi":
        parts.append("hi")
    return ".".join(parts) + ".srt"


def parse_tokens(tokens: Iterable[str]) -> tuple[str | None, frozenset[str]]:
    """Language and flags from the dotted tokens between a video stem and the extension."""
    toks = [t for t in tokens if t]
    language = next(
        (iso for iso in (normalize(t) for t in toks if t.lower() not in FLAG_TOKENS) if iso),
        None,
    )
    return language, frozenset(t.lower() for t in toks)


def find_sidecars(
    video: Path, extra_dirs: Iterable[Path] = (), include_video_dir: bool = True
) -> list[Sidecar]:
    """External subtitle files belonging to `video`, in its folder and any extra dirs.

    A file that belongs to a sibling video with a longer name is not ours: next to
    `Movie.mkv` and `Movie.Extended.mkv`, `Movie.Extended.fr.srt` belongs to the latter.
    """
    prefix = video.stem + "."
    found: list[Sidecar] = []
    folders = [video.parent] if include_video_dir else []
    for folder in (*folders, *extra_dirs):
        try:
            entries = list(folder.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            continue
        longer = [
            e.stem + "."
            for e in entries
            if e.suffix.lower() in VIDEO_EXTS and e.stem != video.stem
            and e.stem.startswith(prefix)
        ]
        for p in entries:
            if p.suffix.lower() not in SUB_EXTS or not p.name.startswith(prefix):
                continue
            if any(p.name.startswith(other) for other in longer):
                continue
            if not p.is_file():
                continue
            middle = p.name[len(prefix) : -len(p.suffix)]
            language, flags = parse_tokens(middle.split("."))
            found.append(Sidecar(p, language, flags))
    return found


def satisfies(sidecar: Sidecar, language: str, variant: str) -> bool:
    """Does an existing sidecar already cover this language and variant?

    A forced target needs a forced sidecar; a full or HI target is covered by any
    non-forced sidecar of the language (Bazarr's `.hi` file counts as full coverage).
    """
    if sidecar.language != language:
        return False
    return sidecar.forced if variant == "forced" else not sidecar.forced
