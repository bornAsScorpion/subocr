"""Turn Radarr, Sonarr and Jellyfin webhook payloads into file events.

Radarr/Sonarr *Connect → Webhook* events used:
- Download (On File Import and On File Upgrade): queue the imported file
- Rename: queue the file under its new path, carry its state across
- MovieFileDelete / EpisodeFileDelete: mark the file gone
- Test: answer OK

Jellyfin's Webhook plugin sends whatever its template says; the adapter reads
NotificationType (ItemAdded), ItemType (Movie/Episode) and either Path or ItemId.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from typing import Any

IMPORT, RENAME, DELETE, TEST, IGNORED = "import", "rename", "delete", "test", "ignored"


@dataclass
class HookEvent:
    kind: str
    paths: list[str] = field(default_factory=list)
    previous: list[str | None] = field(default_factory=list)  # for renames, aligned with paths
    item_id: str | None = None  # Jellyfin item, when no path was sent
    event_type: str = ""


def _file_path(file: dict[str, Any] | None, folder: str | None) -> str | None:
    if not file:
        return None
    if file.get("path"):
        return file["path"]
    if folder and file.get("relativePath"):
        return posixpath.join(folder, file["relativePath"])
    return None


def _arr(payload: dict[str, Any], kind: str) -> HookEvent:
    """Shared Radarr/Sonarr parsing. kind is 'movie' or 'episode'."""
    et = payload.get("eventType") or ""
    parent = payload.get("movie" if kind == "movie" else "series") or {}
    folder = parent.get("folderPath") or parent.get("path")

    if et == "Test":
        return HookEvent(TEST, event_type=et)

    if et == "Download":
        if kind == "movie":
            files = [payload.get("movieFile")]
        else:
            files = payload.get("episodeFiles") or [payload.get("episodeFile")]
        paths = [p for p in (_file_path(f, folder) for f in files) if p]
        return HookEvent(IMPORT, sorted(set(paths)), event_type=et)

    if et == "Rename":
        renamed = payload.get("renamedMovieFiles" if kind == "movie" else "renamedEpisodeFiles")
        renamed = renamed or []
        paths = [r.get("path") for r in renamed if r.get("path")]
        prev = [r.get("previousPath") for r in renamed if r.get("path")]
        return HookEvent(RENAME, paths, prev, event_type=et)

    if et in ("MovieFileDelete", "EpisodeFileDelete"):
        file = payload.get("movieFile" if kind == "movie" else "episodeFile")
        path = _file_path(file, folder)
        return HookEvent(DELETE, [path] if path else [], event_type=et)

    return HookEvent(IGNORED, event_type=et)


def parse_radarr(payload: dict[str, Any]) -> HookEvent:
    return _arr(payload, "movie")


def parse_sonarr(payload: dict[str, Any]) -> HookEvent:
    return _arr(payload, "episode")


def parse_jellyfin(payload: dict[str, Any]) -> HookEvent:
    get = {k.lower(): v for k, v in payload.items()}.get
    et = get("notificationtype") or ""
    item_type = get("itemtype")
    if et and et != "ItemAdded":
        return HookEvent(IGNORED, event_type=et)
    if item_type and item_type not in ("Movie", "Episode"):
        return HookEvent(IGNORED, event_type=et)
    path = get("path")
    return HookEvent(IMPORT, [path] if path else [], item_id=get("itemid"), event_type=et)
