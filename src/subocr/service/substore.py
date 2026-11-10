"""The subtitle store: apply cleanup plans, keep the archive index, restore from it.

Archive layout mirrors the library: a sub archived from
/data/Movies/Film (2000)/Film.fr.srt lands in /archive/Movies/Film (2000)/Film.fr.srt.
Every archived or deleted file gets a row in archive_entries, so restores can find
it by media file, and a restore can be undone by archiving again.
"""

from __future__ import annotations

import contextlib
import logging
import os
import posixpath
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from ..naming import VIDEO_EXTS, find_sidecars, parse_tokens, satisfies
from ..sidecars import ARCHIVE, DELETE, pending, plan_cleanup
from ..writer import write_no_clobber
from .settings import ServiceSettings
from .store import Store

log = logging.getLogger(__name__)

RESTORED, RESTORED_UNVERIFIED = "restored", "restored-unverified"


@dataclass
class CleanupReport:
    mode: str
    archived: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    would_archive: list[str] = field(default_factory=list)
    would_delete: list[str] = field(default_factory=list)
    kept_unknown: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v or k == "mode"}

    @property
    def changed(self) -> bool:
        return bool(self.archived or self.deleted)


class MoveError(OSError):
    """An archive move failed; `step` says where: read-source, write-archive, remove-source."""

    def __init__(self, step: str, exc: OSError):
        super().__init__(exc.errno, f"{step}: {exc.strerror or exc}", exc.filename)
        self.step = step


def _move(src: Path, dest: Path) -> int:
    """Move across filesystems (NAS library -> local archive) without chmod. Returns size."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MoveError("write-archive", exc) from exc
    tmp = dest.parent / f".{dest.name}.{secrets.token_hex(4)}.tmp"
    try:
        try:
            with open(src, "rb") as f:
                data = f.read()
        except OSError as exc:
            raise MoveError("read-source", exc) from exc
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
        except OSError as exc:
            raise MoveError("write-archive", exc) from exc
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    try:
        src.unlink()
    except OSError as exc:
        dest.unlink()  # keep exactly one copy: the original stays in place
        raise MoveError("remove-source", exc) from exc
    return len(data)


def _free_name(dest: Path) -> Path:
    """`dest`, or `name (2).ext`-style variants when it is taken."""
    if not dest.exists():
        return dest
    stem, ext = dest.name[: -len(dest.suffix)] if dest.suffix else dest.name, dest.suffix
    n = 2
    while (candidate := dest.with_name(f"{stem} ({n}){ext}")).exists():
        n += 1
    return candidate


class SubtitleStore:
    def __init__(self, store: Store, settings: ServiceSettings) -> None:
        self.store = store
        self.settings = settings

    # --- paths ----------------------------------------------------------------

    def archive_path_for(self, original: Path) -> Path:
        """Mirror of `original` under ARCHIVE_ROOT, keyed by the library folder's name."""
        orig = posixpath.normpath(str(original))
        for lib in sorted(self.settings.library_paths, key=len, reverse=True):
            if orig.startswith(lib.rstrip("/") + "/"):
                rel = orig[len(lib.rstrip("/")) + 1 :]
                return Path(self.settings.archive_root) / posixpath.basename(lib) / rel
        raise ValueError(f"{original} is not inside a library")

    # --- cleanup ----------------------------------------------------------------

    def plan(self, video: Path, languages: tuple[str, ...]):
        return plan_cleanup(video, languages, self.settings.cleanup_mode,
                            self.settings.archive_unknown, self.settings.orphan_subs)

    def cleanup_pending(self, video: Path, languages: tuple[str, ...]) -> bool:
        """Would cleanup change anything now? (Never true in dry-run or off.)"""
        if self.settings.cleanup_mode not in ("archive", "delete"):
            return False
        return bool(pending(self.plan(video, languages)))

    def restore_pending(self, video: Path, languages: tuple[str, ...]) -> bool:
        """Is an archived sub in a wanted language waiting, with nothing covering it?"""
        entries = [e for e in self._candidates(video) if e["language"] in languages]
        if not entries:
            return False
        existing = find_sidecars(video)
        for e in entries:
            _, flags = parse_tokens(e["suffix"].rsplit(".", 1)[0].split("."))
            variant = "forced" if flags & {"forced", "foreign"} else "full"
            if not any(satisfies(sc, e["language"], variant) for sc in existing):
                return True
        return False

    def cleanup(self, video: Path, languages: tuple[str, ...]) -> CleanupReport:
        mode = self.settings.cleanup_mode
        report = CleanupReport(mode)
        if mode == "off":
            return report
        try:
            video_size: int | None = video.stat().st_size
        except OSError:
            video_size = None
        for act in self.plan(video, languages):
            src = Path(act.path)
            if act.action not in (ARCHIVE, DELETE):
                if act.language is None:
                    report.kept_unknown.append(act.path)
                continue
            if mode == "dry-run":
                (report.would_delete if act.action == DELETE else report.would_archive).append(
                    act.path)
                self.store.add_event(str(video), f"cleanup-would-{act.action}", act.path)
                continue
            entry = {
                "media_path": str(video), "language": act.language,
                "suffix": act.suffix, "original_path": act.path,
                # an orphan came from another release: any later restore is unverified
                "video_name": "" if act.orphan else video.name,
                "video_size": None if act.orphan else video_size,
            }
            try:
                if act.action == ARCHIVE:
                    dest = _free_name(self.archive_path_for(src))
                    size = _move(src, dest)
                    self.store.add_archive_entry(**entry, archive_path=str(dest), size=size,
                                                 action=ARCHIVE, status="archived")
                    self.store.add_event(str(video), "archived", f"{act.path} -> {dest}")
                    report.archived.append(act.path)
                    log.info("archived %s -> %s", src.name, dest)
                else:
                    size = src.stat().st_size
                    try:
                        src.unlink()
                    except OSError as exc:
                        raise MoveError("remove-source", exc) from exc
                    self.store.add_archive_entry(**entry, archive_path=None, size=size,
                                                 action=DELETE, status="deleted")
                    self.store.add_event(str(video), "deleted-subtitle", act.path)
                    report.deleted.append(act.path)
                    log.info("deleted %s", src.name)
            except OSError as exc:
                report.failed.append({"path": act.path, "error": str(exc)})
                self.store.add_event(str(video), "cleanup-failed", f"{act.path}: {exc}")
                log.warning("cleanup of %s failed: %s", act.path, exc)
        return report

    # --- restore ----------------------------------------------------------------

    def _candidates(self, video: Path) -> list:
        """Archived entries for this video, or for the folder it alone occupies.

        The folder fallback covers a movie whose file was replaced by a new release
        (new name, so a new media path) after its old subtitles were archived.
        """
        rows = self.store.archived_for(str(video))
        if rows:
            return rows
        try:
            videos = [p for p in video.parent.iterdir() if p.suffix.lower() in VIDEO_EXTS]
        except OSError:
            return []
        if len(videos) == 1:
            return self.store.archived_in_folder(str(video.parent))
        return []

    def restore_entry(self, entry, video: Path) -> dict:
        """Copy one archived file back next to `video`, renamed to its current stem."""
        if entry["status"] != "archived" or not entry["archive_path"]:
            return {"id": entry["id"], "status": "skipped", "reason": f"entry is {entry['status']}"}
        src = Path(entry["archive_path"])
        dest = video.parent / (video.stem + entry["suffix"])
        try:
            data = src.read_bytes()
        except OSError as exc:
            return {"id": entry["id"], "status": "failed", "reason": f"archive copy: {exc}"}
        try:
            written = write_no_clobber(dest, data)
        except OSError as exc:
            return {"id": entry["id"], "status": "failed", "reason": str(exc)}
        if not written:
            return {"id": entry["id"], "status": "skipped", "reason": f"{dest.name} exists"}
        try:
            size = video.stat().st_size
        except OSError:
            size = None
        same_video = entry["video_name"] == video.name and entry["video_size"] == size
        status = RESTORED if same_video else RESTORED_UNVERIFIED
        self.store.mark_restored(entry["id"], status, str(dest))
        with contextlib.suppress(OSError):
            src.unlink()
        self.store.add_event(str(video), status, f"{src} -> {dest}")
        log.info("%s %s", status, dest.name)
        return {"id": entry["id"], "status": status, "path": str(dest)}

    def restore_wanted(self, video: Path, languages: tuple[str, ...]) -> list[dict]:
        """Before any extraction or OCR: bring back archived subs in wanted languages."""
        existing = find_sidecars(video)
        # Group by name without extension, so a VobSub .idx/.sub pair is restored together
        groups: dict[tuple[str, str], list] = {}
        for entry in self._candidates(video):
            if entry["language"] in languages:
                stem = entry["suffix"].rsplit(".", 1)[0]
                groups.setdefault((entry["language"], stem), []).append(entry)
        results = []
        for (language, stem), entries in groups.items():
            _, flags = parse_tokens(stem.split("."))
            variant = "forced" if flags & {"forced", "foreign"} else "full"
            if any(satisfies(sc, language, variant) for sc in existing):
                continue
            group_results = [self.restore_entry(e, video) for e in entries]
            results += group_results
            if any(r["status"] in (RESTORED, RESTORED_UNVERIFIED) for r in group_results):
                existing = find_sidecars(video)
        return results
