"""Job submission and the single worker that runs them."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from .. import job as core
from .. import ocr
from ..config import Config
from .notify import Notifier
from .settings import ServiceSettings
from .store import Store
from .substore import SubtitleStore

log = logging.getLogger(__name__)

HIGH, LOW = 10, 0  # webhook/API jobs run ahead of the reconcile scan

# text_mode stored on a job: "plugin" = leave text tracks alone, "container" = extract them
SKIP_TEXT, DO_TEXT = "plugin", "container"


class QueueFull(RuntimeError):
    pass


@dataclass
class Submission:
    job_ids: list[int]
    deduplicated: bool


def config_fingerprint(
    cfg: Config, models: Sequence[str], settings: ServiceSettings | None = None
) -> str:
    """Changes when a setting that affects results changes, so the scan re-runs files."""
    payload: dict = {
        "languages": cfg.languages,
        "default_languages": cfg.default_languages,
        "default_marking": cfg.default_marking,
        "text_fallback": cfg.text_fallback,
        "hi_policy": cfg.hi_policy,
        "commentary_regex": cfg.commentary_regex,
        "forced_max_cues_per_min": cfg.forced_max_cues_per_min,
        "ocr_enabled": cfg.ocr_enabled,
        "models": sorted(models),
    }
    if settings is not None:
        payload["cleanup_mode"] = settings.cleanup_mode
        payload["archive_unknown"] = settings.archive_unknown
        payload["orphan_subs"] = settings.orphan_subs
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class JobService:
    def __init__(
        self, store: Store, cfg: Config, settings: ServiceSettings, notifier: Notifier
    ) -> None:
        self.store = store
        self.cfg = cfg
        self.settings = settings
        self.notifier = notifier
        self.subs = SubtitleStore(store, settings)
        self.fingerprint = config_fingerprint(cfg, sorted(ocr.installed_models()), settings)
        self._wake = threading.Event()
        self._stop = threading.Event()

    # --- submission ---------------------------------------------------------

    def _steps(self, source: str, scope: str) -> list[tuple[str, float]]:
        """(text_mode, delay seconds) for each job a submission becomes.

        TEXT_MODE=auto for a new file: OCR now, text extraction after the plugin's
        grace period (only what the plugin did not produce). The reconcile scan sees
        older files the plugin already had its chance at, so it does everything now.
        """
        if scope == "ocr-only" or self.cfg.text_mode == "plugin":
            return [(SKIP_TEXT, 0.0)]
        if self.cfg.text_mode == "auto" and source != "reconcile":
            return [(SKIP_TEXT, 0.0), (DO_TEXT, self.settings.plugin_grace_minutes * 60)]
        return [(DO_TEXT, 0.0)]

    def submit(
        self,
        path: str,
        source: str,
        languages: Sequence[str] | None = None,
        scope: str = "full",
        callback_url: str | None = None,
        low_priority: bool = False,
    ) -> Submission:
        priority = LOW if low_priority else HIGH
        if priority == HIGH and self.store.count_queued(HIGH) >= self.settings.job_queue_limit:
            raise QueueFull(f"{self.settings.job_queue_limit} jobs already queued")
        langs = ",".join(languages) if languages else None
        steps = self._steps(source, scope)
        now = time.time()
        ids: list[int] = []
        deduplicated = False
        for i, (text_mode, delay) in enumerate(steps):
            existing = self.store.find_active(path, text_mode, langs)
            if existing is not None:
                ids.append(existing)
                deduplicated = True
                continue
            # A callback fires once, when the last job of the submission finishes
            cb = callback_url if i == len(steps) - 1 else None
            ids.append(
                self.store.add_job(path, source, text_mode, langs, priority, now + delay, cb)
            )
        self._wake.set()
        return Submission(ids, deduplicated)

    # --- worker -------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run_worker(self) -> None:
        log.info("worker started")
        while not self._stop.is_set():
            if not self.run_once():
                self._wake.wait(timeout=5)
                self._wake.clear()
        log.info("worker stopped")

    def run_once(self) -> bool:
        """Run the next due job, if any. Returns False when nothing was due."""
        job = self.store.next_job()
        if job is None:
            return False
        self._execute(job)
        return True

    def _job_config(self, job) -> Config:
        cfg = replace(self.cfg, text_mode=job["text_mode"])
        if job["languages"]:
            cfg = replace(cfg, languages=tuple(job["languages"].split(",")))
        return cfg

    def _execute(self, job) -> None:
        job_id, path = job["id"], Path(job["path"])
        log.info("job %s: %s (%s, text %s)", job_id, path.name, job["source"], job["text_mode"])
        payload: dict = {"job_id": job_id, "path": str(path)}
        try:
            if not path.is_file():
                self.store.mark_gone(str(path))
                self.store.add_event(str(path), "missing")
                self.store.finish_job(job_id, "missing", None, None)
                payload["outcome"] = "missing"
                return
            job_cfg = self._job_config(job)
            # 1. cheapest first: bring back archived subs in wanted languages
            restored = self.subs.restore_wanted(path, job_cfg.languages)
            # 2. extract / OCR whatever is still missing
            result = core.process_file(path, job_cfg)
            if result.outcome in ("complete", "nothing-to-do") and any(
                r["status"].startswith("restored") for r in restored
            ):
                result.outcome = "restored"
            # 3. cleanup keeps LANGUAGES plus any languages this job asked for, so a job
            #    for ["da"] neither archives English nor undoes its own Danish sub
            keep = tuple(dict.fromkeys(self.cfg.languages + job_cfg.languages))
            cleanup = self.subs.cleanup(path, keep)
            st = path.stat()
            self.store.upsert_media(
                str(path), st.st_size, st.st_mtime, self.fingerprint, result.outcome
            )
            written = [t for t in result.targets if t.status == core.WRITTEN]
            for t in written:
                assert t.path
                self.store.add_output(
                    str(path), t.language, t.variant, t.path,
                    "ocr" if t.kind == "bitmap" else "extracted",
                    _sha256(Path(t.path)), t.quality,
                    "ocr" if t.kind == "bitmap" else "extracted",
                )
            detail = ", ".join(f"{t.language}/{t.variant}:{t.status}" for t in result.targets)
            self.store.add_event(str(path), result.outcome, detail)
            result_dict = result.to_dict()
            result_dict["restored"] = restored
            result_dict["cleanup"] = cleanup.to_dict()
            self.store.finish_job(job_id, result.outcome, json.dumps(result_dict), None)
            payload.update(result_dict, outcome=result.outcome)
            restored_any = any(r["status"].startswith("restored") for r in restored)
            if written or restored_any or cleanup.changed:
                self.notifier.media_updated(str(path))
            log.info("job %s: %s", job_id, result.outcome)
        except Exception as exc:  # one bad file must never stop the worker
            log.exception("job %s failed", job_id)
            self.store.add_event(str(path), "failed", str(exc))
            self.store.finish_job(job_id, "failed", None, str(exc))
            payload.update(outcome="failed", error=str(exc))
        finally:
            if job["callback_url"]:
                self.notifier.callback(job["callback_url"], payload)
