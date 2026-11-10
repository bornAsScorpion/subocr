"""Cleanup rules, seeded with real Tesseract misreads from VobSub OCR."""

import pytest

from subocr.cleanup import clean_subs, clean_text, load_srt, quality_score

N = "♪"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("f Fuckin' posers", f"{N} Fuckin' posers"),
        ("J Here we go, yeah\nHere we go, yeah f", f"{N} Here we go, yeah\nHere we go, yeah {N}"),
        ("~ Yeah Sf", f"{N} Yeah {N}"),
        ("¢ It's almost over now\nAlmost over now £",
         f"{N} It's almost over now\nAlmost over now {N}"),
        ("? For you fuckin' posers\n~ Yeah!", f"{N} For you fuckin' posers\n{N} Yeah!"),
        ("< Yeah\nCome on", f"{N} Yeah\nCome on"),
        ("J Better call your boys\n‘Cause I'm coming IL",
         f"{N} Better call your boys\n‘Cause I'm coming {N}"),
        ("(TIRES SCREECH\nTHEN CAR CRASHES}", "(TIRES SCREECH\nTHEN CAR CRASHES)"),
        ("! don't think your mammy would\nbe too pleased about that now.",
         "I don't think your mammy would\nbe too pleased about that now."),
        ("l think so.", "I think so."),
    ],
)
def test_real_misreads(raw, expected):
    assert clean_text(raw, "en") == expected


@pytest.mark.parametrize(
    "line",
    [
        "What a beautiful fucking day.",
        "Yes, Mr President.",
        "It's clear that when he arrived\nin Malagawi on May 14,",
        "Mr President, it's hard to believe...",
        "IL FAUT PARTIR",  # all-caps line starting with a real word
        "Okay!",
    ],
)
def test_clean_lines_unchanged(line):
    assert clean_text(line, "en") == line


def test_i_rule_only_for_english():
    assert clean_text("l est parti", "fr") == "l est parti"


def test_whitespace_and_empty_lines():
    assert clean_text("  Hello   there \n\n  ", "en") == "Hello there"


SRT = """1
00:00:10,093 --> 00:00:11,073
f Fuckin' posers

2
00:00:11,386 --> 00:00:13,466
J Here we go, yeah
Here we go, yeah f

3
00:00:15,000 --> 00:00:16,000


4
00:01:31,008 --> 00:01:33,938
! don't think your mammy would
be too pleased about that now.
"""


def test_clean_file_and_score(tmp_path):
    p = tmp_path / "ocr.srt"
    p.write_text(SRT, encoding="utf-8")
    subs = clean_subs(load_srt(p), "en")
    assert len(subs) == 3  # empty cue dropped
    assert subs[0].plaintext == f"{N} Fuckin' posers"
    assert subs[2].plaintext.startswith("I don't")
    assert quality_score(subs, "en") == 1.0
    out = subs.to_string("srt")
    assert "00:00:10,093 --> 00:00:11,073" in out  # timing untouched


def test_score_drops_with_junk(tmp_path):
    p = tmp_path / "junk.srt"
    p.write_text("1\n00:00:01,000 --> 00:00:02,000\nWh@t a b#autiful d%y \\ ^\n", encoding="utf-8")
    assert quality_score(load_srt(p), "en") < 0.5
