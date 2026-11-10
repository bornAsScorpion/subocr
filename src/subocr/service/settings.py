"""Service settings, read from environment variables (*arr-style)."""

from __future__ import annotations

import logging
import os
import posixpath
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..config import ConfigError

log = logging.getLogger(__name__)

_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _norm(path: str) -> str:
    path = path.strip()
    if not path.startswith("/"):
        raise ConfigError(f"path must be absolute: {path!r}")
    return posixpath.normpath(path)


def parse_library_paths(value: str) -> tuple[str, ...]:
    paths = tuple(_norm(p) for p in value.split(",") if p.strip())
    if not paths:
        raise ConfigError("LIBRARY_PATHS: at least one library path is required")
    return paths


def parse_path_mappings(value: str) -> tuple[tuple[str, str], ...]:
    """'remote:local,remote2:local2' -> ((remote, local), ...)."""
    out = []
    for pair in value.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            raise ConfigError(f"PATH_MAPPINGS: expected remote:local, got {pair!r}")
        remote, local = pair.split(":", 1)
        out.append((_norm(remote), _norm(local)))
    return tuple(out)


def parse_reconcile_at(value: str) -> str | None:
    v = value.strip().lower()
    if v in ("off", "never", "none", ""):
        return None
    if not _HHMM.match(v):
        raise ConfigError(f"RECONCILE_AT: expected HH:MM or off, got {value!r}")
    return v


def _int(value: str, name: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name}: not an integer: {value!r}") from exc


def _float(value: str, name: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{name}: not a number: {value!r}") from exc


@dataclass(frozen=True)
class ServiceSettings:
    library_paths: tuple[str, ...]
    path_mappings: tuple[tuple[str, str], ...] = ()
    api_key: str = ""
    host: str = "0.0.0.0"
    port: int = 8585
    config_dir: str = "/config"
    job_queue_limit: int = 500
    reconcile_at: str | None = "03:30"
    plugin_grace_minutes: float = 10.0
    job_retention_days: int = 30
    jellyfin_url: str | None = None
    jellyfin_api_key: str | None = None
    cleanup_mode: str = "dry-run"
    archive_root: str = "/archive"
    archive_unknown: bool = False
    orphan_subs: str = "delete"

    def __post_init__(self) -> None:
        root = posixpath.normpath(self.archive_root)
        for lib in self.library_paths:
            if root == lib or root.startswith(lib.rstrip("/") + "/") \
                    or lib.startswith(root.rstrip("/") + "/"):
                raise ConfigError(
                    f"ARCHIVE_ROOT {self.archive_root} overlaps library {lib}: the archive must "
                    "be outside every library, or Jellyfin would index archived subtitles")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ServiceSettings:
        env = os.environ if env is None else env
        if not env.get("LIBRARY_PATHS"):
            raise ConfigError("LIBRARY_PATHS is required, e.g. /data/Movies,/data/Shows")
        kw: dict[str, object] = {"library_paths": parse_library_paths(env["LIBRARY_PATHS"])}
        if v := env.get("PATH_MAPPINGS"):
            kw["path_mappings"] = parse_path_mappings(v)
        if v := env.get("PORT"):
            kw["port"] = _int(v, "PORT")
        if v := env.get("CONFIG_DIR"):
            kw["config_dir"] = v
        if v := env.get("JOB_QUEUE_LIMIT"):
            kw["job_queue_limit"] = _int(v, "JOB_QUEUE_LIMIT")
        if "RECONCILE_AT" in env:
            kw["reconcile_at"] = parse_reconcile_at(env["RECONCILE_AT"])
        if v := env.get("PLUGIN_GRACE_MINUTES"):
            kw["plugin_grace_minutes"] = _float(v, "PLUGIN_GRACE_MINUTES")
        if v := env.get("JOB_RETENTION_DAYS"):
            kw["job_retention_days"] = _int(v, "JOB_RETENTION_DAYS")
        if v := env.get("JELLYFIN_URL"):
            kw["jellyfin_url"] = v.rstrip("/")
        if v := env.get("JELLYFIN_API_KEY"):
            kw["jellyfin_api_key"] = v
        if v := env.get("CLEANUP_MODE"):
            from ..sidecars import CLEANUP_MODES

            v = v.strip().lower()
            if v not in CLEANUP_MODES:
                raise ConfigError(f"CLEANUP_MODE: expected one of {', '.join(CLEANUP_MODES)}, "
                                  f"got {v!r}")
            kw["cleanup_mode"] = v
        if v := env.get("ARCHIVE_ROOT"):
            kw["archive_root"] = _norm(v)
        if v := env.get("ORPHAN_SUBS"):
            from ..sidecars import ORPHAN_POLICIES

            v = v.strip().lower()
            if v not in ORPHAN_POLICIES:
                raise ConfigError(f"ORPHAN_SUBS: expected one of {', '.join(ORPHAN_POLICIES)}, "
                                  f"got {v!r}")
            kw["orphan_subs"] = v
        if v := env.get("ARCHIVE_UNKNOWN"):
            kw["archive_unknown"] = v.strip().lower() in ("1", "true", "yes", "on")
        settings = cls(**kw)  # type: ignore[arg-type]
        api_key = env.get("API_KEY") or load_or_create_api_key(Path(settings.config_dir))
        from dataclasses import replace

        return replace(settings, api_key=api_key)

    @property
    def db_path(self) -> Path:
        return Path(self.config_dir) / "subocr.db"


def load_or_create_api_key(config_dir: Path) -> str:
    """Use /config/api_key, creating it on first start (like the *arrs do)."""
    key_file = config_dir / "api_key"
    if key_file.exists():
        key = key_file.read_text().strip()
        if key:
            return key
    config_dir.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(16)
    key_file.write_text(key + "\n")
    key_file.chmod(0o600)
    log.warning("API_KEY not set; generated one and saved it to %s", key_file)
    return key
