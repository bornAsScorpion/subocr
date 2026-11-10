"""Cleanup planning and sidecar name parsing."""

from pathlib import Path

import pytest

from subocr.naming import find_sidecars, parse_tokens
from subocr.sidecars import plan_cleanup, suffix_of

STEM = "Coastline 2008 1080p BluRay"


def _folder(tmp_path, names, video=f"{STEM}.mkv"):
    (tmp_path / video).touch()
    for n in names:
        (tmp_path / n).touch()
    return tmp_path / video


@pytest.mark.parametrize(("tokens", "lang", "flags"), [
    (["en"], "en", {"en"}),
    (["en", "hi"], "en", {"en", "hi"}),
    (["hi"], None, {"hi"}),  # a bare .hi. is a flag, never Hindi
    (["hin"], "hi", {"hin"}),
    (["pt-BR"], "pt", {"pt-br"}),
    (["English", "SDH"], "en", {"english", "sdh"}),
    (["2", "Danish"], "da", {"2", "danish"}),
    (["vie"], "vi", {"vie"}),
    (["forced"], None, {"forced"}),
    (["xyz"], None, {"xyz"}),
])
def test_parse_tokens(tokens, lang, flags):
    assert parse_tokens(tokens) == (lang, frozenset(flags))


def test_plan_keeps_wanted_archives_rest(tmp_path):
    video = _folder(tmp_path, [
        f"{STEM}.en.srt", f"{STEM}.en.forced.srt", f"{STEM}.en.sdh.srt", f"{STEM}.ru.ass",
        f"{STEM}.da.srt", f"{STEM}.ger.srt", f"{STEM}.heb.srt", f"{STEM}.chi.srt",
        f"{STEM}.iTunes.srt", "unrelated.fr.srt",
    ])
    plan = {Path(a.path).name: a for a in plan_cleanup(video, ("en", "ru"))}
    keep = {n for n, a in plan.items() if a.action == "keep"}
    archive = {n for n, a in plan.items() if a.action == "archive"}
    assert keep == {f"{STEM}.en.srt", f"{STEM}.en.forced.srt", f"{STEM}.en.sdh.srt",
                    f"{STEM}.ru.ass", f"{STEM}.iTunes.srt"}
    assert archive == {f"{STEM}.da.srt", f"{STEM}.ger.srt", f"{STEM}.heb.srt",
                       f"{STEM}.chi.srt"}
    assert plan["unrelated.fr.srt"].orphan and plan["unrelated.fr.srt"].action == "delete"
    assert "not recognised" in plan[f"{STEM}.iTunes.srt"].reason


def test_plan_modes_and_unknown(tmp_path):
    video = _folder(tmp_path, [f"{STEM}.da.srt", f"{STEM}.iTunes.srt"])
    actions = {Path(a.path).name: a.action for a in plan_cleanup(video, ("en",), "delete")}
    assert actions == {f"{STEM}.da.srt": "delete", f"{STEM}.iTunes.srt": "keep"}
    actions = {Path(a.path).name: a.action
               for a in plan_cleanup(video, ("en",), "delete", archive_unknown=True)}
    assert actions[f"{STEM}.iTunes.srt"] == "archive"  # unknown is never deleted


def test_sibling_video_files_are_not_ours(tmp_path):
    video = _folder(tmp_path, ["Movie.fr.srt", "Movie.Extended.fr.srt"], video="Movie.mkv")
    (tmp_path / "Movie.Extended.mkv").touch()
    assert [s.path.name for s in find_sidecars(video)] == ["Movie.fr.srt"]
    other = [s.path.name for s in find_sidecars(tmp_path / "Movie.Extended.mkv")]
    assert other == ["Movie.Extended.fr.srt"]


def test_suffix_of(tmp_path):
    assert suffix_of(Path("/m/A.B.mkv"), Path("/m/A.B.en.forced.srt")) == ".en.forced.srt"


def test_upgrade_leftovers_are_orphans(tmp_path):
    """Radarr replaced a.mkv with b.mkv; a.*.srt matches no video any more."""
    for n in ("a.en.srt", "a.fr.srt", "a.en.forced.srt", "b.mkv", "b.en.srt"):
        (tmp_path / n).touch()
    plan = {Path(a.path).name: a for a in plan_cleanup(tmp_path / "b.mkv", ("en",), "archive")}
    assert plan["b.en.srt"].action == "keep" and not plan["b.en.srt"].orphan
    for n in ("a.en.srt", "a.fr.srt", "a.en.forced.srt"):
        assert plan[n].orphan and plan[n].action == "delete"  # even wanted languages
    keep_orphans = plan_cleanup(tmp_path / "b.mkv", ("en",), "archive", orphans="keep")
    assert [Path(a.path).name for a in keep_orphans] == ["b.en.srt"]


def test_season_folder_orphans(tmp_path):
    """Subs of any episode are not orphans; only names matching no episode are."""
    for n in ("S01E01.mkv", "S01E02.mkv", "S01E01.fr.srt", "S01E02.en.srt",
              "old.release.S01E03.it.srt"):
        (tmp_path / n).touch()
    orphans = [a for a in plan_cleanup(tmp_path / "S01E01.mkv", ("en",), "archive") if a.orphan]
    assert [Path(a.path).name for a in orphans] == ["old.release.S01E03.it.srt"]


def test_folder_without_video_is_left_alone(tmp_path):
    from subocr.sidecars import find_orphans

    (tmp_path / "a.fr.srt").touch()
    assert find_orphans(tmp_path) == []


@pytest.mark.parametrize(("name", "lang", "suffix"), [
    ("the.ambassador.s04e01.1080p.web.h264[source].mkv[tag].fr.srt", "fr",
     ".fr.srt"),
    ("Old Release.en.forced.srt", "en", ".en.forced.srt"),
    ("Old Release.Italian.sdh.srt", "it", ".Italian.sdh.srt"),
    ("De Niro Story 2019.srt", None, ".srt"),  # 'De' far from the end is not German
])
def test_orphan_suffix(name, lang, suffix):
    from subocr.sidecars import orphan_suffix

    got_lang, _, got_suffix = orphan_suffix(Path("/x") / name)
    assert (got_lang, got_suffix) == (lang, suffix)
