"""Cleanup planning: which external subtitles next to a video to keep, archive or delete.

Pure and read-only; the service applies the plan (service/substore.py).

Rules:
- a sidecar in a wanted language (LANGUAGES) is always kept, with any flags
  (forced, hi, sdh, default) and in any format;
- a sidecar in another known language is archived (or deleted in delete mode);
- a sidecar whose language cannot be read from its name is never deleted: kept,
  or archived when ARCHIVE_UNKNOWN is on;
- an orphan, a subtitle in the folder whose name matches no video there (left
  behind when Radarr/Sonarr replaced a.mkv with b.mkv), follows ORPHAN_SUBS:
  delete (default), archive or keep. Jellyfin only shows sidecars whose name
  starts with the video's, so an orphan is never shown anyway.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from .languages import normalize
from .naming import FLAG_TOKENS, SUB_EXTS, VIDEO_EXTS, Sidecar, find_sidecars

KEEP, ARCHIVE, DELETE = "keep", "archive", "delete"
CLEANUP_MODES = ("off", "dry-run", "archive", "delete")
ORPHAN_POLICIES = ("delete", "archive", "keep")


@dataclass(frozen=True)
class SidecarAction:
    path: str
    language: str | None
    flags: list[str]
    action: str  # keep | archive | delete
    reason: str
    suffix: str = ""  # `.fr.srt`: what follows the video stem; used to restore under a new name
    orphan: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def suffix_of(video: Path, sidecar: Path) -> str:
    """The part of a sidecar's name after the video stem: `.en.forced.srt`."""
    return sidecar.name[len(video.stem):]


def orphan_suffix(path: Path) -> tuple[str | None, frozenset[str], str]:
    """Language, flags and suffix of an orphan, read from the end of its name.

    `a.b.c.2020.fr.forced.srt` -> ("fr", {"fr", "forced"}, ".fr.forced.srt"). Only the
    last three tokens are considered, so words in the old release name are not mistaken
    for languages.
    """
    tokens = path.name[: -len(path.suffix)].split(".")
    tail = tokens[-3:]
    for i, tok in enumerate(tail):
        if tok.lower() not in FLAG_TOKENS and normalize(tok):
            rest = tail[i:]
            return normalize(tok), frozenset(t.lower() for t in rest), \
                "." + ".".join(rest) + path.suffix
    flags = frozenset(t.lower() for t in tail if t.lower() in FLAG_TOKENS)
    return None, flags, path.suffix


def find_orphans(folder: Path) -> list[Path]:
    """Subtitle files in `folder` whose name starts with no video's name there.

    A folder with no video is left alone: the video may be mid-move.
    """
    try:
        entries = [e for e in folder.iterdir() if e.is_file()]
    except OSError:
        return []
    prefixes = [e.stem + "." for e in entries if e.suffix.lower() in VIDEO_EXTS]
    if not prefixes:
        return []
    return sorted(
        e for e in entries
        if e.suffix.lower() in SUB_EXTS and not any(e.name.startswith(p) for p in prefixes)
    )


def _decide(
    sc: Sidecar, languages: Iterable[str], mode: str, archive_unknown: bool
) -> tuple[str, str]:
    remove = DELETE if mode == "delete" else ARCHIVE  # dry-run previews archive mode
    if sc.language is None:
        if archive_unknown:
            return ARCHIVE, "language not recognised in the file name (ARCHIVE_UNKNOWN)"
        return KEEP, "language not recognised in the file name"
    if sc.language in languages:
        return KEEP, f"wanted language {sc.language}"
    return remove, f"language {sc.language} not in LANGUAGES"


def plan_cleanup(
    video: Path,
    languages: Iterable[str],
    mode: str = "dry-run",
    archive_unknown: bool = False,
    orphans: str = "delete",
) -> list[SidecarAction]:
    """Planned action for every external subtitle of `video`, plus the folder's orphans."""
    langs = tuple(languages)
    actions = []
    for sc in sorted(find_sidecars(video), key=lambda s: s.path.name):
        action, reason = _decide(sc, langs, mode, archive_unknown)
        actions.append(SidecarAction(str(sc.path), sc.language, sorted(sc.flags), action,
                                     reason, suffix_of(video, sc.path)))
    if orphans != KEEP:
        for path in find_orphans(video.parent):
            language, flags, suffix = orphan_suffix(path)
            actions.append(SidecarAction(
                str(path), language, sorted(flags), orphans,
                "orphan: no video in the folder matches its name (ORPHAN_SUBS)",
                suffix, orphan=True))
    return actions


def pending(actions: Iterable[SidecarAction]) -> list[SidecarAction]:
    """Actions that would change something on disk."""
    return [a for a in actions if a.action in (ARCHIVE, DELETE)]
