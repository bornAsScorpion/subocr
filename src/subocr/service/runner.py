"""`subocr serve`: wire the pieces together and run the HTTP server."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from ..config import Config
from ..models import prepare_from_env
from .api import Services, create_app
from .jobs import JobService
from .notify import Notifier
from .paths import PathPolicy
from .reconcile import Reconciler
from .settings import ServiceSettings
from .store import Store

log = logging.getLogger(__name__)


def build_services(settings: ServiceSettings, cfg: Config) -> Services:
    Path(settings.config_dir).mkdir(parents=True, exist_ok=True)
    store = Store(settings.db_path)
    requeued = store.requeue_running()
    if requeued:
        log.warning("requeued %d jobs interrupted by the last shutdown", requeued)
    policy = PathPolicy(settings.library_paths, settings.path_mappings)
    notifier = Notifier(settings, policy)
    jobs = JobService(store, cfg, settings, notifier)
    reconciler = Reconciler(store, jobs, settings)
    return Services(settings, cfg, store, policy, notifier, jobs, reconciler)


def serve() -> None:
    import uvicorn

    settings = ServiceSettings.from_env()
    cfg = Config.from_env()
    if cfg.ocr_enabled:
        prepare_from_env(cfg.languages)  # before build_services: the fingerprint lists models
    svc = build_services(settings, cfg)
    for lib in settings.library_paths:
        if not Path(lib).is_dir():
            log.warning("library path %s does not exist (check the volume mounts)", lib)

    stop = threading.Event()
    threading.Thread(target=svc.jobs.run_worker, name="worker", daemon=True).start()
    threading.Thread(target=svc.reconciler.run_scheduler, args=(stop,), name="scheduler",
                     daemon=True).start()
    log.info("subocr listening on %s:%s, libraries %s, languages %s, text mode %s",
             settings.host, settings.port, ",".join(settings.library_paths),
             ",".join(cfg.languages), cfg.text_mode)
    try:
        uvicorn.run(create_app(svc), host=settings.host, port=settings.port,
                    log_level="info", access_log=False)
    finally:
        stop.set()
        svc.jobs.stop()
