from dataclasses import replace

from subocr.config import Config
from subocr.rules import FORCED, FULL, HI, build_plan, classify
from tests.conftest import make_info


def _targets(plan):
    return [(t.language, t.variant, t.track.index) for t in plan.targets]


def test_vobsub_full_and_forced_commentary_skipped(harbor):
    plan = build_plan(harbor, Config())
    assert _targets(plan) == [("en", FULL, 3), ("en", FORCED, 4)]
    assert plan.decisions[5] == "skip: commentary"


def test_pgs_mislabeled_forced_is_full_and_text_preferred(courier):
    cfg = Config()
    assert classify(courier.tracks[0], courier, cfg) == FULL
    plan = build_plan(courier, cfg)
    # SRT twin (#4) beats the PGS track (#3); no forced target at all
    assert _targets(plan) == [("en", FULL, 4)]
    assert any("forced flag ignored" in n for n in plan.notes)
    assert plan.decisions[5] == "skip: language de not wanted"


def test_pgs_three_languages(courier):
    plan = build_plan(courier, Config(languages=("en", "de", "hu")))
    assert _targets(plan) == [("en", FULL, 4), ("de", FULL, 6), ("hu", FULL, 8)]


def test_sparse_forced_track_stays_forced(stream):
    info = make_info([
        stream(2, "hdmv_pgs_subtitle", "eng", frames=1200),
        stream(3, "hdmv_pgs_subtitle", "eng", title="Forced", forced=1, frames=40),
    ])
    assert _targets(build_plan(info, Config())) == [("en", FULL, 2), ("en", FORCED, 3)]


def test_forced_flag_checked_against_peer_when_no_duration(stream):
    info = make_info([
        stream(2, "hdmv_pgs_subtitle", "eng", frames=1000),
        stream(3, "hdmv_pgs_subtitle", "eng", forced=1, frames=990),
    ])
    info = replace(info, duration=None)
    assert [t.variant for t in build_plan(info, Config()).targets] == [FULL]


def test_untagged_skipped(stream):
    info = make_info([stream(2, "hdmv_pgs_subtitle", None)])
    plan = build_plan(info, Config())
    assert plan.targets == []
    assert plan.decisions[2].startswith("skip: untagged")


def test_commentary_by_disposition(stream):
    info = make_info([stream(2, "subrip", "eng", comment=1)])
    assert build_plan(info, Config()).targets == []


def test_hi_policies(stream):
    info = make_info([
        stream(2, "hdmv_pgs_subtitle", "eng"),
        stream(3, "subrip", "eng", title="SDH"),
    ])
    assert _targets(build_plan(info, Config())) == [("en", FULL, 2)]
    assert _targets(build_plan(info, Config(hi_policy="both"))) == [
        ("en", FULL, 2), ("en", HI, 3)]
    assert _targets(build_plan(info, Config(hi_policy="skip"))) == [("en", FULL, 2)]


def test_hi_used_when_only_track(stream):
    info = make_info([stream(3, "subrip", "eng", hi=1)])
    assert _targets(build_plan(info, Config())) == [("en", HI, 3)]


def test_hi_policy_skip_drops_lone_hi(stream):
    info = make_info([stream(3, "subrip", "eng", hi=1)])
    assert build_plan(info, Config(hi_policy="skip")).targets == []


def test_text_preferred_over_bitmap_then_stream_order(stream):
    info = make_info([
        stream(2, "dvd_subtitle", "eng"),
        stream(3, "subrip", "eng"),
        stream(4, "ass", "eng"),
    ])
    assert _targets(build_plan(info, Config())) == [("en", FULL, 3)]


def test_language_order_follows_config(stream):
    info = make_info([stream(2, "subrip", "eng"), stream(3, "subrip", "rus")])
    plan = build_plan(info, Config(languages=("ru", "en")))
    assert [t.language for t in plan.targets] == ["ru", "en"]


def test_text_fallback_first_stream(stream):
    info = make_info([stream(2, "hdmv_pgs_subtitle", "fre"), stream(3, "subrip", "spa")])
    assert build_plan(info, Config()).targets == []
    plan = build_plan(info, Config(text_fallback="first-stream"))
    assert _targets(plan) == [("es", FULL, 3)]


def test_unsupported_codec(stream):
    info = make_info([stream(2, "eia_608", "eng")])
    plan = build_plan(info, Config())
    assert plan.targets == []
    assert plan.decisions[2].startswith("skip: unsupported codec")


def test_pgs_packets_count_as_half_cues(stream):
    # 300 PGS packets over 100 min = 150 cues = 1.5 cues/min: still a real forced track
    info = make_info([stream(3, "hdmv_pgs_subtitle", "eng", forced=1, frames=300)],
                     duration_min=100)
    assert info.tracks[0].cues == 150
    assert classify(info.tracks[0], info, Config()) == FORCED
    # the same packet count on a text track would be 3 cues/min: treated as full
    info = make_info([stream(3, "subrip", "eng", forced=1, frames=300)], duration_min=100)
    assert classify(info.tracks[0], info, Config()) == FULL
