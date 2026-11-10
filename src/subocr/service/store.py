"""SQLite state: the job queue, processed files, outputs we wrote, and an event log."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    source TEXT NOT NULL,
    text_mode TEXT NOT NULL,
    languages TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
    not_before REAL NOT NULL,
    created REAL NOT NULL,
    started REAL,
    finished REAL,
    callback_url TEXT,
    outcome TEXT,
    result TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS jobs_pick ON jobs (status, priority, not_before);
CREATE INDEX IF NOT EXISTS jobs_path ON jobs (path, status);

CREATE TABLE IF NOT EXISTS media_files (
    path TEXT PRIMARY KEY,
    size INTEGER,
    mtime REAL,
    fingerprint TEXT,
    last_run REAL,
    outcome TEXT,
    gone INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS subtitle_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_path TEXT NOT NULL,
    language TEXT NOT NULL,
    variant TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    content_hash TEXT,
    quality REAL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt REAL,
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS outputs_media ON subtitle_outputs (media_path);

CREATE TABLE IF NOT EXISTS archive_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_path TEXT NOT NULL,
    language TEXT,
    suffix TEXT NOT NULL,
    original_path TEXT NOT NULL,
    archive_path TEXT,
    video_name TEXT NOT NULL,
    video_size INTEGER,
    size INTEGER,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    created REAL NOT NULL,
    restored REAL,
    restored_path TEXT
);
CREATE INDEX IF NOT EXISTS archive_media ON archive_entries (media_path, status);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time REAL NOT NULL,
    media_path TEXT,
    event TEXT NOT NULL,
    detail TEXT
);
"""

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"


class Store:
    def __init__(self, db_path: Path | str):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        if str(db_path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)

    def _q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _x(self, sql: str, params: tuple = ()) -> int:
        """Execute; returns the number of rows changed."""
        with self._lock:
            return self._conn.execute(sql, params).rowcount

    def _insert(self, sql: str, params: tuple = ()) -> int:
        """Execute an INSERT; returns the new row id."""
        with self._lock:
            cur = self._conn.execute(sql, params)
            assert cur.lastrowid is not None
            return cur.lastrowid

    # --- jobs ---------------------------------------------------------------

    def requeue_running(self) -> int:
        """Jobs interrupted by a restart go back to the queue."""
        return self._x("UPDATE jobs SET status=?, started=NULL WHERE status=?", (QUEUED, RUNNING))

    def find_active(self, path: str, text_mode: str, languages: str | None) -> int | None:
        rows = self._q(
            "SELECT id FROM jobs WHERE path=? AND text_mode=? AND languages IS ? "
            "AND status IN (?, ?) ORDER BY id LIMIT 1",
            (path, text_mode, languages, QUEUED, RUNNING),
        )
        return rows[0]["id"] if rows else None

    def add_job(
        self,
        path: str,
        source: str,
        text_mode: str,
        languages: str | None,
        priority: int,
        not_before: float,
        callback_url: str | None = None,
    ) -> int:
        return self._insert(
            "INSERT INTO jobs (path, source, text_mode, languages, priority, status, "
            "not_before, created, callback_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (path, source, text_mode, languages, priority, QUEUED, not_before, time.time(),
             callback_url),
        )

    def count_queued(self, min_priority: int = 0) -> int:
        rows = self._q(
            "SELECT COUNT(*) AS n FROM jobs WHERE status=? AND priority>=?", (QUEUED, min_priority)
        )
        return rows[0]["n"]

    def next_job(self, now: float | None = None) -> sqlite3.Row | None:
        """Claim the next due job: highest priority first, then oldest."""
        now = time.time() if now is None else now
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE status=? AND not_before<=? "
                "ORDER BY priority DESC, not_before, id LIMIT 1",
                (QUEUED, now),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE jobs SET status=?, started=? WHERE id=?", (RUNNING, now, row["id"])
            )
            return self._conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()

    def finish_job(self, job_id: int, outcome: str, result: str | None, error: str | None) -> None:
        status = FAILED if error or outcome == "failed" else DONE
        self._x(
            "UPDATE jobs SET status=?, finished=?, outcome=?, result=?, error=? WHERE id=?",
            (status, time.time(), outcome, result, error, job_id),
        )

    def get_job(self, job_id: int) -> sqlite3.Row | None:
        rows = self._q("SELECT * FROM jobs WHERE id=?", (job_id,))
        return rows[0] if rows else None

    def list_jobs(self, status: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
        if status:
            return self._q(
                "SELECT * FROM jobs WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)
            )
        return self._q("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))

    def queue_counts(self) -> dict[str, int]:
        rows = self._q("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    # --- media files --------------------------------------------------------

    def upsert_media(
        self, path: str, size: int, mtime: float, fingerprint: str, outcome: str
    ) -> None:
        self._x(
            "INSERT INTO media_files (path, size, mtime, fingerprint, last_run, outcome, gone) "
            "VALUES (?, ?, ?, ?, ?, ?, 0) ON CONFLICT(path) DO UPDATE SET size=excluded.size, "
            "mtime=excluded.mtime, fingerprint=excluded.fingerprint, "
            "last_run=excluded.last_run, outcome=excluded.outcome, gone=0",
            (path, size, mtime, fingerprint, time.time(), outcome),
        )

    def get_media(self, path: str) -> sqlite3.Row | None:
        rows = self._q("SELECT * FROM media_files WHERE path=?", (path,))
        return rows[0] if rows else None

    def media_paths_under(self, root: str) -> list[str]:
        prefix = root.rstrip("/") + "/"
        rows = self._q(
            "SELECT path FROM media_files WHERE gone=0 AND substr(path, 1, ?)=?",
            (len(prefix), prefix),
        )
        return [r["path"] for r in rows]

    # --- archive ------------------------------------------------------------

    def add_archive_entry(self, **kw) -> int:
        cols = ("media_path", "language", "suffix", "original_path", "archive_path",
                "video_name", "video_size", "size", "action", "status")
        return self._insert(
            f"INSERT INTO archive_entries ({', '.join(cols)}, created) "
            f"VALUES ({', '.join('?' for _ in cols)}, ?)",
            (*(kw.get(c) for c in cols), time.time()),
        )

    def get_archive_entry(self, entry_id: int) -> sqlite3.Row | None:
        rows = self._q("SELECT * FROM archive_entries WHERE id=?", (entry_id,))
        return rows[0] if rows else None

    def archived_for(self, media_path: str) -> list[sqlite3.Row]:
        return self._q(
            "SELECT * FROM archive_entries WHERE media_path=? AND status='archived' ORDER BY id",
            (media_path,),
        )

    def archived_in_folder(self, folder: str) -> list[sqlite3.Row]:
        """Archived entries whose original file sat directly in `folder`."""
        prefix = folder.rstrip("/") + "/"
        rows = self._q(
            "SELECT * FROM archive_entries WHERE status='archived' "
            "AND substr(original_path, 1, ?)=? ORDER BY id",
            (len(prefix), prefix),
        )
        return [r for r in rows if "/" not in r["original_path"][len(prefix):]]

    def mark_restored(self, entry_id: int, status: str, restored_path: str) -> None:
        self._x(
            "UPDATE archive_entries SET status=?, restored=?, restored_path=? WHERE id=?",
            (status, time.time(), restored_path, entry_id),
        )

    def list_archive(
        self, path_prefix: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[sqlite3.Row]:
        sql, params = "SELECT * FROM archive_entries WHERE 1=1", []
        if path_prefix:
            prefix = path_prefix.rstrip("/")
            sql += " AND (media_path=? OR substr(media_path, 1, ?)=?)"
            params += [prefix, len(prefix) + 1, prefix + "/"]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return self._q(sql, tuple(params))

    def mark_gone(self, path: str) -> None:
        self._x("UPDATE media_files SET gone=1 WHERE path=?", (path,))

    def rename_media(self, old: str, new: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM media_files WHERE path=?", (new,))
            self._conn.execute("UPDATE media_files SET path=? WHERE path=?", (new, old))
            self._conn.execute(
                "UPDATE subtitle_outputs SET media_path=? WHERE media_path=?", (new, old)
            )
            self._conn.execute(
                "UPDATE archive_entries SET media_path=? WHERE media_path=?", (new, old)
            )

    # --- outputs and events -------------------------------------------------

    def add_output(
        self,
        media_path: str,
        language: str,
        variant: str,
        path: str,
        source: str,
        content_hash: str | None,
        quality: float | None,
        status: str,
    ) -> None:
        self._x(
            "INSERT INTO subtitle_outputs (media_path, language, variant, path, source, "
            "content_hash, quality, status, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET media_path=excluded.media_path, "
            "language=excluded.language, variant=excluded.variant, source=excluded.source, "
            "content_hash=excluded.content_hash, quality=excluded.quality, "
            "status=excluded.status, created=excluded.created",
            (media_path, language, variant, path, source, content_hash, quality, status,
             time.time()),
        )

    def outputs_for(self, media_path: str) -> list[sqlite3.Row]:
        return self._q("SELECT * FROM subtitle_outputs WHERE media_path=? ORDER BY id",
                       (media_path,))

    def add_event(self, media_path: str | None, event: str, detail: str = "") -> None:
        self._x(
            "INSERT INTO events (time, media_path, event, detail) VALUES (?, ?, ?, ?)",
            (time.time(), media_path, event, detail),
        )

    def recent_events(self, limit: int = 50, event: str | None = None) -> list[sqlite3.Row]:
        if event:
            return self._q("SELECT * FROM events WHERE event=? ORDER BY id DESC LIMIT ?",
                           (event, limit))
        return self._q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))

    def prune(self, before: float) -> None:
        """Drop finished jobs and events older than `before`."""
        self._x("DELETE FROM jobs WHERE status IN (?, ?) AND finished<?", (DONE, FAILED, before))
        self._x("DELETE FROM events WHERE time<?", (before,))


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None
