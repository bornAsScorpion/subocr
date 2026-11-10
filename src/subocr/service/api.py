"""HTTP API v1: system status, jobs, reconcile, and webhook adapters."""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import __version__, ocr
from ..config import Config
from ..languages import is_known, normalize
from ..naming import VIDEO_EXTS
from ..probe import BITMAP_CODECS, TEXT_CODECS
from .jobs import JobService, QueueFull
from .notify import Notifier
from .paths import PathPolicy, PathRejected
from .reconcile import Reconciler
from .settings import ServiceSettings
from .store import Store, row_to_dict
from .webhooks import (
    DELETE,
    IMPORT,
    RENAME,
    TEST,
    HookEvent,
    parse_jellyfin,
    parse_radarr,
    parse_sonarr,
)

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: ServiceSettings
    cfg: Config
    store: Store
    policy: PathPolicy
    notifier: Notifier
    jobs: JobService
    reconciler: Reconciler


class JobRequest(BaseModel):
    path: str = Field(description="Video file path, as the caller sees it")
    languages: list[str] | None = Field(default=None, description="Default: LANGUAGES")
    scope: Literal["full", "ocr-only"] = "full"
    callback_url: str | None = None
    source: str = Field(default="api", max_length=64)


class ReconcileRequest(BaseModel):
    paths: list[str] | None = Field(default=None, description="Default: all LIBRARY_PATHS")
    force: bool = Field(default=False,
                        description="Queue every video found, even ones the database says are done")


class PreviewRequest(BaseModel):
    path: str = Field(description="A video file or a folder, as the caller sees it")


def _job_view(row) -> dict[str, Any] | None:
    job = row_to_dict(row)
    if job and job.get("result"):
        job["result"] = json.loads(job["result"])
    return job


def create_app(svc: Services) -> FastAPI:
    app = FastAPI(title="subocr", version=__version__,
                  description="Fills subtitle gaps next to media files.")

    def require_key(
        x_api_key: str | None = Header(default=None),
        apikey: str | None = Query(default=None),
    ) -> None:
        given = x_api_key or apikey or ""
        if not secrets.compare_digest(given.encode(), svc.settings.api_key.encode()):
            raise HTTPException(status_code=401, detail="missing or wrong API key")

    api = APIRouter(prefix="/api/v1", dependencies=[Depends(require_key)])

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # --- system ---------------------------------------------------------------

    @api.get("/system/status")
    def status() -> dict[str, Any]:
        return {
            "version": __version__,
            "ocr_enabled": svc.cfg.ocr_enabled,
            "ocr_models": sorted(ocr.installed_models()),
            "languages": list(svc.cfg.languages),
            "text_mode": svc.cfg.text_mode,
            "formats": {"text": sorted(TEXT_CODECS), "bitmap": sorted(BITMAP_CODECS)},
            "libraries": list(svc.settings.library_paths),
            "queue": svc.store.queue_counts(),
            "reconcile": {
                "at": svc.settings.reconcile_at,
                "running": svc.reconciler.running,
                "last_run": svc.reconciler.last_run,
            },
            "cleanup": {
                "mode": svc.settings.cleanup_mode,
                "orphan_subs": svc.settings.orphan_subs,
                "archive_root": svc.settings.archive_root,
                "archive_unknown": svc.settings.archive_unknown,
            },
        }

    # --- jobs -----------------------------------------------------------------

    def _submit(path: str, source: str, **kw) -> list[int]:
        try:
            return svc.jobs.submit(path, source, **kw).job_ids
        except QueueFull as exc:
            raise HTTPException(status_code=429, detail=str(exc),
                                headers={"Retry-After": "60"}) from exc

    @api.post("/jobs", status_code=202)
    def submit_job(req: JobRequest) -> dict[str, Any]:
        try:
            local = svc.policy.resolve(req.path)
        except PathRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not os.path.isfile(local):
            raise HTTPException(status_code=404, detail=f"file not found: {local}")
        languages = None
        if req.languages:
            languages = []
            for raw in req.languages:
                iso = normalize(raw)
                if iso is None or not is_known(iso):
                    raise HTTPException(status_code=422, detail=f"unknown language {raw!r}")
                languages.append(iso)
        try:
            sub = svc.jobs.submit(local, req.source, languages=languages, scope=req.scope,
                                  callback_url=req.callback_url)
        except QueueFull as exc:
            raise HTTPException(status_code=429, detail=str(exc),
                                headers={"Retry-After": "60"}) from exc
        return {"id": sub.job_ids[0], "job_ids": sub.job_ids, "deduplicated": sub.deduplicated,
                "path": local}

    @api.get("/jobs/{job_id}")
    def get_job(job_id: int) -> dict[str, Any]:
        job = _job_view(svc.store.get_job(job_id))
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        return job

    @api.get("/jobs")
    def list_jobs(status: str | None = None, limit: int = Query(default=50, le=500)) -> list:
        return [_job_view(r) for r in svc.store.list_jobs(status, limit)]

    @api.get("/events")
    def events(limit: int = Query(default=50, le=500), event: str | None = None) -> list:
        """Recent events, newest first; `event` filters, e.g. cleanup-would-archive."""
        return [row_to_dict(r) for r in svc.store.recent_events(limit, event)]

    # --- cleanup and archive ----------------------------------------------------

    @api.post("/cleanup/preview")
    def cleanup_preview(req: PreviewRequest) -> dict[str, Any]:
        """What cleanup would do for a video or every video under a folder. Changes nothing."""
        try:
            local = svc.policy.resolve(req.path)
        except PathRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        root = Path(local)
        if root.is_file():
            videos = [root]
        elif root.is_dir():
            videos = sorted(p for p in root.rglob("*")
                            if p.suffix.lower() in VIDEO_EXTS and p.is_file()
                            and not any(part.startswith(".") for part in p.parts))
        else:
            raise HTTPException(status_code=404, detail=f"not found: {local}")
        mode = svc.settings.cleanup_mode
        items, totals = [], {"keep": 0, "archive": 0, "delete": 0}
        seen: set[str] = set()  # a season folder's orphans would repeat for every episode
        for video in videos:
            plan = [a for a in svc.jobs.subs.plan(video, svc.cfg.languages)
                    if a.path not in seen]
            seen.update(a.path for a in plan)
            for act in plan:
                totals[act.action] += 1
            if plan:
                items.append({"video": str(video), "sidecars": [a.to_dict() for a in plan]})
        return {"mode": mode, "languages": list(svc.cfg.languages),
                "applies": mode in ("archive", "delete"), "totals": totals,
                "videos_scanned": len(videos), "items": items}

    @api.get("/archive")
    def archive_list(path: str | None = None, status: str | None = None,
                     limit: int = Query(default=100, le=1000)) -> list:
        """Archived and deleted subtitles, newest first; `path` limits to a file or folder."""
        prefix = None
        if path:
            try:
                prefix = svc.policy.resolve(path)
            except PathRejected as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return [row_to_dict(r) for r in svc.store.list_archive(prefix, status, limit)]

    @api.post("/archive/{entry_id}/restore")
    def archive_restore(entry_id: int) -> dict[str, Any]:
        """Put one archived subtitle back next to its video, whatever LANGUAGES says.

        Cleanup would archive it again on the video's next run unless its language is
        added to LANGUAGES.
        """
        entry = svc.store.get_archive_entry(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="no such archive entry")
        video = Path(entry["media_path"])
        if not video.is_file():
            folder = Path(entry["original_path"]).parent
            vids = [p for p in folder.glob("*") if p.suffix.lower() in VIDEO_EXTS]
            if len(vids) != 1:
                raise HTTPException(status_code=409, detail=(
                    f"{video.name} is gone and {folder} does not hold exactly one video"))
            video = vids[0]
        result = svc.jobs.subs.restore_entry(entry, video)
        if result["status"] == "failed":
            raise HTTPException(status_code=500, detail=result.get("reason"))
        if result["status"].startswith("restored"):
            svc.notifier.media_updated(str(video))
        return result

    # --- reconcile ------------------------------------------------------------

    @api.post("/reconcile", status_code=202)
    def reconcile(req: ReconcileRequest | None = None) -> dict[str, Any]:
        roots = None
        if req and req.paths:
            try:
                roots = [svc.policy.resolve(p) for p in req.paths]
            except PathRejected as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        force = bool(req and req.force)
        if not svc.reconciler.scan_in_background(roots, force):
            raise HTTPException(status_code=409, detail="a reconcile scan is already running")
        return {"status": "started", "force": force,
                "paths": roots or list(svc.settings.library_paths)}

    # --- webhooks -------------------------------------------------------------

    def _handle(event: HookEvent, source: str) -> dict[str, Any]:
        if event.kind == TEST:
            return {"status": "ok", "event": event.event_type}
        paths = list(event.paths)
        if event.kind == IMPORT and not paths and event.item_id:
            remote = svc.notifier.lookup_path(event.item_id)
            if remote:
                paths = [remote]
        accepted: list[str] = []
        rejected: list[dict[str, str]] = []
        job_ids: list[int] = []
        for i, raw in enumerate(paths):
            try:
                local = svc.policy.resolve(raw)
            except PathRejected as exc:
                rejected.append({"path": raw, "reason": str(exc)})
                continue
            if event.kind in (IMPORT, RENAME) and not os.path.isfile(local):
                rejected.append({"path": raw, "reason": f"file not found: {local}"})
                continue
            accepted.append(local)
            if event.kind == DELETE:
                svc.store.mark_gone(local)
                svc.store.add_event(local, "deleted", source)
                continue
            if event.kind == RENAME and i < len(event.previous) and event.previous[i]:
                try:
                    svc.store.rename_media(svc.policy.resolve(event.previous[i] or ""), local)
                except PathRejected:
                    pass
            if event.kind in (IMPORT, RENAME):
                job_ids += _submit(local, source)
        if rejected:
            log.warning("%s webhook: rejected paths %s", source, rejected)
        return {"status": "accepted" if accepted else "ignored", "event": event.event_type,
                "paths": accepted, "rejected": rejected, "job_ids": job_ids}

    async def _payload(request: Request, source: str) -> dict[str, Any]:
        """JSON or form-encoded body as a dict; a clear 400 instead of a 500 otherwise."""
        body = await request.body()
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if not body.strip():
            log.warning("%s webhook: empty body (content-type %r)", source, ctype or None)
            raise HTTPException(status_code=400, detail=(
                "empty request body: send the webhook payload as JSON "
                "(in Jellyfin's Webhook plugin, fill in the template or enable "
                "'Send All Properties')"))
        if ctype in ("application/x-www-form-urlencoded", "multipart/form-data"):
            form = await request.form()
            return {k: v for k, v in form.items() if isinstance(v, str)}
        try:
            data = json.loads(body)
        except ValueError as exc:
            log.warning("%s webhook: body is not JSON (content-type %r): %.200r",
                        source, ctype or None, body)
            raise HTTPException(status_code=400, detail=f"body is not valid JSON: {exc}") \
                from exc
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        return data

    def _body_doc(example: dict[str, Any]) -> dict[str, Any]:
        """Show a JSON body editor with an example on the /docs page."""
        return {"requestBody": {"required": True, "content": {"application/json": {
            "schema": {"type": "object", "additionalProperties": True},
            "example": example}}}}

    radarr_example = {"eventType": "Download",
                      "movie": {"folderPath": "/data/Movies/Harbor Lights (2011)"},
                      "movieFile": {"relativePath": "Harbor Lights (2011).mkv"}}
    sonarr_example = {"eventType": "Download", "series": {"path": "/data/Shows/Show"},
                      "episodeFile": {"relativePath": "Season 01/Show S01E01.mkv"}}
    jellyfin_example = {"NotificationType": "ItemAdded", "ItemType": "Movie",
                        "ItemId": "<jellyfin item id>",
                        "Path": "/mnt/media/Movies/Harbor Lights (2011)/Harbor Lights (2011).mkv"}

    @api.post("/webhooks/radarr", openapi_extra=_body_doc(radarr_example))
    async def radarr(request: Request) -> dict[str, Any]:
        return _handle(parse_radarr(await _payload(request, "radarr")), "radarr")

    @api.post("/webhooks/sonarr", openapi_extra=_body_doc(sonarr_example))
    async def sonarr(request: Request) -> dict[str, Any]:
        return _handle(parse_sonarr(await _payload(request, "sonarr")), "sonarr")

    @api.post("/webhooks/jellyfin", openapi_extra=_body_doc(jellyfin_example))
    async def jellyfin(request: Request) -> dict[str, Any]:
        return _handle(parse_jellyfin(await _payload(request, "jellyfin")), "jellyfin")

    app.include_router(api)
    return app
