from subocr.service.webhooks import (
    DELETE,
    IGNORED,
    IMPORT,
    RENAME,
    TEST,
    parse_jellyfin,
    parse_radarr,
    parse_sonarr,
)

MOVIE = {"id": 1, "title": "Harbor Lights", "year": 2011,
         "folderPath": "/data/Movies/Harbor Lights (2011)"}


def test_radarr_download_with_path():
    ev = parse_radarr({"eventType": "Download", "isUpgrade": False, "movie": MOVIE,
                       "movieFile": {"relativePath": "Harbor Lights (2011).mkv",
                                     "path": "/data/Movies/Harbor Lights (2011)/"
                                             "Harbor Lights (2011).mkv"}})
    assert ev.kind == IMPORT
    assert ev.paths == ["/data/Movies/Harbor Lights (2011)/Harbor Lights (2011).mkv"]


def test_radarr_download_relative_only():
    ev = parse_radarr({"eventType": "Download", "movie": MOVIE,
                       "movieFile": {"relativePath": "Harbor Lights (2011).mkv"}})
    assert ev.paths == ["/data/Movies/Harbor Lights (2011)/Harbor Lights (2011).mkv"]


def test_radarr_rename_delete_test_other():
    ev = parse_radarr({"eventType": "Rename", "movie": MOVIE, "renamedMovieFiles": [
        {"path": "/data/Movies/G/new.mkv", "previousPath": "/data/Movies/G/old.mkv"}]})
    assert (ev.kind, ev.paths, ev.previous) == (RENAME, ["/data/Movies/G/new.mkv"],
                                                ["/data/Movies/G/old.mkv"])
    ev = parse_radarr({"eventType": "MovieFileDelete", "movie": MOVIE,
                       "movieFile": {"path": "/data/Movies/G/x.mkv"}})
    assert (ev.kind, ev.paths) == (DELETE, ["/data/Movies/G/x.mkv"])
    assert parse_radarr({"eventType": "Test"}).kind == TEST
    assert parse_radarr({"eventType": "Grab", "movie": MOVIE}).kind == IGNORED


def test_sonarr_download_single_and_multi():
    series = {"id": 3, "title": "Show", "path": "/data/Shows/Show"}
    ev = parse_sonarr({"eventType": "Download", "series": series,
                       "episodeFile": {"relativePath": "Season 01/Show S01E01.mkv"}})
    assert ev.paths == ["/data/Shows/Show/Season 01/Show S01E01.mkv"]
    ev = parse_sonarr({"eventType": "Download", "series": series, "episodeFiles": [
        {"path": "/data/Shows/Show/Season 01/a.mkv"},
        {"path": "/data/Shows/Show/Season 01/b.mkv"}]})
    assert ev.paths == ["/data/Shows/Show/Season 01/a.mkv", "/data/Shows/Show/Season 01/b.mkv"]


def test_sonarr_rename_and_delete():
    series = {"path": "/data/Shows/Show"}
    ev = parse_sonarr({"eventType": "Rename", "series": series, "renamedEpisodeFiles": [
        {"path": "/data/Shows/Show/n.mkv", "previousPath": "/data/Shows/Show/o.mkv"}]})
    assert ev.kind == RENAME and ev.previous == ["/data/Shows/Show/o.mkv"]
    ev = parse_sonarr({"eventType": "EpisodeFileDelete", "series": series,
                       "episodeFile": {"relativePath": "x.mkv"}})
    assert (ev.kind, ev.paths) == (DELETE, ["/data/Shows/Show/x.mkv"])


def test_jellyfin():
    ev = parse_jellyfin({"NotificationType": "ItemAdded", "ItemType": "Movie",
                         "ItemId": "abc", "Path": "/mnt/media/Movies/A/A.mkv"})
    assert (ev.kind, ev.paths, ev.item_id) == (IMPORT, ["/mnt/media/Movies/A/A.mkv"], "abc")
    ev = parse_jellyfin({"notificationType": "ItemAdded", "itemType": "Episode", "itemId": "x"})
    assert (ev.kind, ev.paths, ev.item_id) == (IMPORT, [], "x")
    assert parse_jellyfin({"NotificationType": "PlaybackStart"}).kind == IGNORED
    assert parse_jellyfin({"NotificationType": "ItemAdded", "ItemType": "Audio"}).kind == IGNORED
