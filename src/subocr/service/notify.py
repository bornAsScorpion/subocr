"""Outbound calls: Jellyfin refresh and item lookup, and job completion callbacks.

All of them are best-effort: a failure is logged and never affects the job.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.parse
import urllib.request

from .. import __version__
from .paths import PathPolicy
from .settings import ServiceSettings

log = logging.getLogger(__name__)
TIMEOUT = 10


def _request(url: str, data: dict | None = None, headers: dict[str, str] | None = None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
    req.add_header("User-Agent", "subocr")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (configured URLs)
        raw = resp.read()
        return json.loads(raw) if raw else None


class Notifier:
    def __init__(self, settings: ServiceSettings, policy: PathPolicy) -> None:
        self.settings = settings
        self.policy = policy

    @property
    def jellyfin_enabled(self) -> bool:
        return bool(self.settings.jellyfin_url and self.settings.jellyfin_api_key)

    def _jellyfin_headers(self) -> dict[str, str]:
        """Jellyfin 12 accepts only the Authorization header (legacy X-Emby-Token and
        api_key were removed); this form also works on Jellyfin 10.x."""
        token = (self.settings.jellyfin_api_key or "").replace('"', "")
        return {"Authorization": (
            f'MediaBrowser Client="subocr", Device="subocr", DeviceId="subocr", '
            f'Version="{__version__}", Token="{token}"')}

    def media_updated(self, local_path: str) -> None:
        """Tell Jellyfin the item changed, so it picks up new sidecars without a full scan."""
        if not self.jellyfin_enabled:
            return
        remote = self.policy.to_remote(local_path)
        url = f"{self.settings.jellyfin_url}/Library/Media/Updated"
        try:
            _request(url, {"Updates": [{"Path": remote, "UpdateType": "Modified"}]},
                     self._jellyfin_headers())
        except Exception as exc:
            log.warning("Jellyfin refresh failed for %s: %s", remote, exc)
            return
        log.info("Jellyfin refresh requested for %s", remote)

    def lookup_path(self, item_id: str) -> str | None:
        """Path of a Jellyfin item, as Jellyfin sees it (for webhooks that carry only ItemId)."""
        if not self.jellyfin_enabled:
            return None
        query = urllib.parse.urlencode({"ids": item_id, "Fields": "Path"})
        try:
            data = _request(f"{self.settings.jellyfin_url}/Items?{query}",
                            headers=self._jellyfin_headers())
        except Exception as exc:
            log.warning("Jellyfin lookup failed for item %s: %s", item_id, exc)
            return None
        items = (data or {}).get("Items") or []
        path = items[0].get("Path") if items else None
        log.info("Jellyfin item %s resolved to %s", item_id, path or "no path")
        return path

    def callback(self, url: str, payload: dict) -> None:
        """One fire-and-forget POST with the job outcome."""
        def send() -> None:
            try:
                _request(url, payload)
            except Exception as exc:
                log.warning("callback to %s failed: %s", url, exc)
                return
            log.info("callback sent to %s (job %s, %s)", url, payload.get("job_id"),
                     payload.get("outcome"))

        threading.Thread(target=send, name="callback", daemon=True).start()
