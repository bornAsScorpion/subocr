"""Post-OCR cleanup and a quality score.

The rules come from real Tesseract misreads of English VobSub subtitles:
- Tesseract has no good model for the music note, so ♪ comes out as a stray
  token at the start or end of a lyric line: f, J, ¢, ~, <, £, ? / f, Sf, £, IL.
- "I" at the start of a line is read as "!" (also "l" or "|").
- A closing parenthesis is read as "}".

OCR output carries no styling tags, so rules work on plain text.
"""

from __future__ import annotations

import re
from pathlib import Path

import pysubs2

NOTE = "♪"

# Lone junk token at line start, followed by text. Two-letter tokens like "IL" are
# deliberately not matched at the start: they can be real words in all-caps lines.
_LEAD_NOTE = re.compile(r"^(?:[fJ¢~<£?♫])\s+(?=\S)")
# Lone junk token at line end, after text
_TRAIL_NOTE = re.compile(r"(?<=\S)\s+(?:[fJ¢£♫]|IL|Sf)$")
# "! don't", "l think", "| was": a standalone !, l or | before a lowercase word
_I_MISREAD = re.compile(r"(?:^|(?<=\s))[!l|](?=\s+[a-z])")
_SPACES = re.compile(r"[ \t]{2,}")


def _fix_brackets(text: str) -> str:
    if text.count("(") > text.count(")") and "}" in text:
        i = text.rfind("}")
        text = text[:i] + ")" + text[i + 1 :]
    if text.count(")") > text.count("(") and "{" in text:
        text = text.replace("{", "(", 1)
    return text


def clean_text(text: str, language: str) -> str:
    """Clean one cue's plain text. Lines are separated by newlines."""
    lines: list[str] = []
    for raw in text.split("\n"):
        line = _SPACES.sub(" ", raw.strip())
        if not line:
            continue
        line = _LEAD_NOTE.sub(NOTE + " ", line)
        line = _TRAIL_NOTE.sub(" " + NOTE, line)
        if language == "en":
            line = _I_MISREAD.sub("I", line)
        lines.append(line)
    return _fix_brackets("\n".join(lines))


def clean_subs(subs: pysubs2.SSAFile, language: str) -> pysubs2.SSAFile:
    for event in subs:
        event.plaintext = clean_text(event.plaintext, language)
    subs.events = [e for e in subs.events if e.plaintext.strip()]
    return subs


def load_srt(path: Path) -> pysubs2.SSAFile:
    return pysubs2.load(str(path), encoding="utf-8", format_="srt")


_PUNCT = "\"'’‘“”.,!?;:()[]-—…«»¿¡"
_ALLOWED_SINGLES_EN = frozenset({"a", "A", "I", "O", NOTE, "&", "-", "—", "…"})


def _suspicious(token: str, language: str) -> bool:
    core = token.strip(_PUNCT)
    if not core:
        return token not in {NOTE, "-", "—", "...", "…"}
    if not all(ch.isalnum() or ch in "'’-" for ch in core):
        return core != NOTE
    if language == "en" and len(core) == 1 and core not in _ALLOWED_SINGLES_EN:
        return not core.isdigit()
    return False


def quality_score(subs: pysubs2.SSAFile, language: str) -> float:
    """Share of tokens that look like real words, 0..1.

    Tesseract word confidence is not exposed by subtile-ocr's CLI, so this is a
    junk-token heuristic: good OCR scores above 0.99.
    """
    tokens = [tok for e in subs for tok in e.plaintext.split()]
    if not tokens:
        return 0.0
    bad = sum(1 for tok in tokens if _suspicious(tok, language))
    return round(1 - bad / len(tokens), 4)
