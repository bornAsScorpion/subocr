from subocr.service import notify
from subocr.service.notify import Notifier
from subocr.service.paths import PathPolicy
from subocr.service.settings import ServiceSettings


def _notifier():
    settings = ServiceSettings(library_paths=("/data/Movies",), api_key="k",
                               jellyfin_url="http://jf:8096", jellyfin_api_key="abc123")
    return Notifier(settings, PathPolicy(("/data/Movies",), (("/mnt/media", "/data"),)))


def test_jellyfin_auth_uses_authorization_header():
    headers = _notifier()._jellyfin_headers()
    assert list(headers) == ["Authorization"]  # no legacy X-Emby-Token
    assert headers["Authorization"].startswith("MediaBrowser ")
    assert 'Token="abc123"' in headers["Authorization"]


def test_media_updated_sends_jellyfin_path(monkeypatch):
    calls = []
    monkeypatch.setattr(notify, "_request", lambda url, data=None, headers=None:
                        calls.append((url, data, headers)))
    _notifier().media_updated("/data/Movies/A (2000)/A.mkv")
    url, data, headers = calls[0]
    assert url == "http://jf:8096/Library/Media/Updated"
    assert data == {"Updates": [{"Path": "/mnt/media/Movies/A (2000)/A.mkv",
                                 "UpdateType": "Modified"}]}
    assert "Authorization" in headers
