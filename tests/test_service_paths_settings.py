import pytest

from subocr.config import ConfigError
from subocr.service.paths import PathPolicy, PathRejected
from subocr.service.reconcile import Reconciler
from subocr.service.settings import ServiceSettings

LIBS = ("/data/Movies", "/data/Shows")
MAPS = (("/mnt/media", "/data"),)


def test_mapping_jellyfin_to_local_and_back():
    p = PathPolicy(LIBS, MAPS)
    assert p.to_local("/mnt/media/Movies/A (2000)/A.mkv") == "/data/Movies/A (2000)/A.mkv"
    assert p.to_remote("/data/Movies/A (2000)/A.mkv") == "/mnt/media/Movies/A (2000)/A.mkv"
    assert p.to_local("/data/Shows/B/S01E01.mkv") == "/data/Shows/B/S01E01.mkv"  # *arr paths
    assert p.to_local("/mnt/mediaX/Movies/a.mkv") == "/mnt/mediaX/Movies/a.mkv"  # prefix only


def test_longest_mapping_wins():
    p = PathPolicy(LIBS, (("/mnt", "/elsewhere"), ("/mnt/media", "/data")))
    assert p.to_local("/mnt/media/Movies/a.mkv") == "/data/Movies/a.mkv"


@pytest.mark.parametrize(
    "path",
    ["/data/Movies/../../etc/passwd", "/etc/passwd", "/data/Music/a.mkv", "relative/a.mkv",
     "/data/MoviesX/a.mkv"],
)
def test_rejects_outside_libraries(path):
    with pytest.raises(PathRejected):
        PathPolicy(LIBS, MAPS).resolve(path)


def test_accepts_inside():
    assert PathPolicy(LIBS, MAPS).resolve("/mnt/media/Shows/B/S01E01.mkv") == \
        "/data/Shows/B/S01E01.mkv"


def test_settings_from_env(tmp_path):
    s = ServiceSettings.from_env({
        "LIBRARY_PATHS": "/data/Movies, /data/Shows",
        "PATH_MAPPINGS": "/mnt/media:/data",
        "API_KEY": "secret",
        "RECONCILE_AT": "04:15",
        "CONFIG_DIR": str(tmp_path),
    })
    assert s.library_paths == LIBS
    assert s.path_mappings == MAPS
    assert s.api_key == "secret"
    assert s.reconcile_at == "04:15"


def test_settings_generates_api_key(tmp_path):
    env = {"LIBRARY_PATHS": "/data/Movies", "CONFIG_DIR": str(tmp_path)}
    first = ServiceSettings.from_env(env).api_key
    assert len(first) == 32
    assert ServiceSettings.from_env(env).api_key == first  # persisted, stable
    assert (tmp_path / "api_key").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize(
    "env",
    [{}, {"LIBRARY_PATHS": "data/Movies"}, {"LIBRARY_PATHS": "/d", "PATH_MAPPINGS": "nocolon"},
     {"LIBRARY_PATHS": "/d", "RECONCILE_AT": "25:00"}],
)
def test_settings_errors(env, tmp_path):
    env = {"CONFIG_DIR": str(tmp_path), "API_KEY": "k", **env}
    with pytest.raises(ConfigError):
        ServiceSettings.from_env(env)


def test_reconcile_off(tmp_path):
    s = ServiceSettings.from_env({"LIBRARY_PATHS": "/d", "RECONCILE_AT": "off", "API_KEY": "k",
                                  "CONFIG_DIR": str(tmp_path)})
    assert s.reconcile_at is None


def test_next_run():
    from datetime import datetime

    now = datetime(2026, 11, 5, 19, 0)
    assert Reconciler.next_run("03:30", now) == datetime(2026, 11, 6, 3, 30)
    assert Reconciler.next_run("21:00", now) == datetime(2026, 11, 5, 21, 0)
