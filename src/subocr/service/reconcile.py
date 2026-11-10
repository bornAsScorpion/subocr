"""The reconcile scan: backfill and safety net for anything the webhooks missed."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

from .jobs import JobService, QueueFull
from .settings import ServiceSettings
from .store import Store

log = logging.getLogger(__name__)

VIDEO_EXTS = frozenset({".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".webm"})
SKIP_DIRS = frozenset({"@eaDir", "#recycle", "lost+found"})
RETRY_OUTCOMES = frozenset({"failed"})


class Reconciler:
    def __init__(self, store: Store, jobs: JobService, settings: ServiceSettings) -> None:
        self.store = store
        self.jobs = jobs
        self.settings = settings
        self._running = threading.Lock()
        self.last_run: dict | None = None

    @property
    def running(self) -> bool:
        return self._running.locked()

    def _needs_run(self, path: str, st: os.stat_result) -> str | None:
        """Why this video should run again, or None."""
        row = self.store.get_media(path)
        if row is None or row["gone"]:
            return "new"
        if row["size"] != st.st_size or row["mtime"] != st.st_mtime:
            return "changed"
        if row["fingerprint"] != self.jobs.fingerprint:
            return "settings"  # settings or installed OCR models changed
        if row["outcome"] in RETRY_OUTCOMES:
            return "retry"
        # The video is unchanged, but its folder may not be: subs added later (Bazarr,
        # a release upgrade's leftovers) or a cleanup that failed last time.
        video, languages = Path(path), self.jobs.cfg.languages
        if self.jobs.subs.cleanup_pending(video, languages):
            return "cleanup"
        if self.jobs.subs.restore_pending(video, languages):
            return "restore"
        return None

    def scan(self, roots: Sequence[str] | None = None, force: bool = False) -> dict:
        """Walk the libraries and queue files that are new, changed or need a re-run.

        force: queue every video found, whatever the database says.
        """
        if not self._running.acquire(blocking=False):
            return {"status": "already-running"}
        started = time.time()
        counts = {"seen": 0, "queued": 0, "unchanged": 0, "gone": 0, "errors": 0}
        reasons: dict[str, int] = {}
        try:
            for root in roots or self.settings.library_paths:
                seen: set[str] = set()
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")
                                   and d not in SKIP_DIRS]
                    for name in filenames:
                        if os.path.splitext(name)[1].lower() not in VIDEO_EXTS:
                            continue
                        path = os.path.join(dirpath, name)
                        seen.add(path)
                        counts["seen"] += 1
                        try:
                            st = os.stat(path)
                        except OSError:
                            counts["errors"] += 1
                            continue
                        try:
                            reason = "forced" if force else self._needs_run(path, st)
                        except OSError:
                            counts["errors"] += 1
                            continue
                        if reason is None:
                            counts["unchanged"] += 1
                            continue
                        reasons[reason] = reasons.get(reason, 0) + 1
                        try:
                            self.jobs.submit(path, "reconcile", low_priority=True)
                            counts["queued"] += 1
                        except QueueFull:  # low priority never hits the limit; defensive
                            counts["errors"] += 1
                for path in self.store.media_paths_under(root):
                    if path not in seen:
                        self.store.mark_gone(path)
                        counts["gone"] += 1
            retention = self.settings.job_retention_days * 86400
            self.store.prune(time.time() - retention)
            counts["seconds"] = round(time.time() - started, 1)
            if reasons:
                counts["reasons"] = reasons  # type: ignore[assignment]
            self.store.add_event(None, "reconcile", str(counts))
            log.info("reconcile: %s", counts)
            self.last_run = {"finished": time.time(), **counts}
            return {"status": "done", **counts}
        finally:
            self._running.release()

    def scan_in_background(self, roots: Sequence[str] | None = None, force: bool = False) -> bool:
        if self.running:
            return False
        threading.Thread(target=self.scan, args=(roots, force), name="reconcile",
                         daemon=True).start()
        return True

    # --- schedule -----------------------------------------------------------

    @staticmethod
    def next_run(at: str, now: datetime | None = None) -> datetime:
        """Next local time HH:MM after `now`."""
        now = now or datetime.now()
        hour, minute = (int(x) for x in at.split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return target if target > now else target + timedelta(days=1)

    def run_scheduler(self, stop: threading.Event) -> None:
        if not self.settings.reconcile_at:
            log.info("reconcile scan schedule is off")
            return
        while not stop.is_set():
            due = self.next_run(self.settings.reconcile_at)
            log.info("next reconcile scan at %s", due.isoformat(timespec="minutes"))
            while not stop.is_set() and datetime.now() < due:
                stop.wait(min(60.0, max(1.0, (due - datetime.now()).total_seconds())))
            if not stop.is_set():
                try:
                    self.scan()
                except Exception:
                    log.exception("reconcile scan failed")
