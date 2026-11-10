"""ISO 639 language helpers.

Config, filenames and internal state use ISO 639-1 (``en``). Matroska tags use
ISO 639-2 (``eng``, sometimes both ``ger`` and ``deu`` forms). Tesseract model
names use ISO 639-2/T (``deu``, ``fra``).
"""

from __future__ import annotations

# ISO 639-1 -> ISO 639-2 codes (bibliographic first, terminologic second where they differ)
_ISO2: dict[str, tuple[str, ...]] = {
    "en": ("eng",),
    "ru": ("rus",),
    "uk": ("ukr",),
    "de": ("ger", "deu"),
    "fr": ("fre", "fra"),
    "es": ("spa",),
    "it": ("ita",),
    "pt": ("por",),
    "nl": ("dut", "nld"),
    "hu": ("hun",),
    "pl": ("pol",),
    "cs": ("cze", "ces"),
    "sk": ("slo", "slk"),
    "sv": ("swe",),
    "da": ("dan",),
    "no": ("nor",),
    "fi": ("fin",),
    "el": ("gre", "ell"),
    "tr": ("tur",),
    "ro": ("rum", "ron"),
    "bg": ("bul",),
    "hr": ("hrv",),
    "sr": ("srp",),
    "he": ("heb",),
    "ar": ("ara",),
    "ja": ("jpn",),
    "ko": ("kor",),
    "zh": ("chi", "zho"),
    "vi": ("vie",),
    "th": ("tha",),
    "id": ("ind",),
    "ms": ("may", "msa"),
    "tl": ("tgl", "fil"),
    "sl": ("slv",),
    "et": ("est",),
    "lv": ("lav",),
    "lt": ("lit",),
    "is": ("ice", "isl"),
    "ca": ("cat",),
    "eu": ("baq", "eus"),
    "gl": ("glg",),
    "sq": ("alb", "sqi"),
    "mk": ("mac", "mkd"),
    "bs": ("bos",),
    "be": ("bel",),
    "ka": ("geo", "kat"),
    "hy": ("arm", "hye"),
    "az": ("aze",),
    "kk": ("kaz",),
    "fa": ("per", "fas"),
    "ur": ("urd",),
    "hi": ("hin",),
    "bn": ("ben",),
    "ta": ("tam",),
    "te": ("tel",),
}

# English language names seen in release filenames ("Movie.English.srt")
_NAMES: dict[str, str] = {
    "english": "en", "russian": "ru", "ukrainian": "uk", "german": "de", "french": "fr",
    "spanish": "es", "italian": "it", "portuguese": "pt", "dutch": "nl", "hungarian": "hu",
    "polish": "pl", "czech": "cs", "slovak": "sk", "swedish": "sv", "danish": "da",
    "norwegian": "no", "finnish": "fi", "greek": "el", "turkish": "tr", "romanian": "ro",
    "bulgarian": "bg", "croatian": "hr", "serbian": "sr", "hebrew": "he", "arabic": "ar",
    "japanese": "ja", "korean": "ko", "chinese": "zh", "vietnamese": "vi", "thai": "th",
    "indonesian": "id", "malay": "ms", "filipino": "tl", "tagalog": "tl", "slovenian": "sl",
    "estonian": "et", "latvian": "lv", "lithuanian": "lt", "icelandic": "is", "catalan": "ca",
    "basque": "eu", "galician": "gl", "albanian": "sq", "macedonian": "mk", "bosnian": "bs",
    "belarusian": "be", "georgian": "ka", "armenian": "hy", "azerbaijani": "az",
    "kazakh": "kk", "persian": "fa", "farsi": "fa", "urdu": "ur", "hindi": "hi",
    "bengali": "bn", "tamil": "ta", "telugu": "te", "brazilian": "pt", "castilian": "es",
    "flemish": "nl", "mandarin": "zh", "cantonese": "zh", "simplified": "zh",
    "traditional": "zh",
}

# Tesseract model names where they differ from the last ISO 639-2 code
_TESSERACT: dict[str, str] = {"zh": "chi_sim"}

_UNDETERMINED = {"", "und", "zxx", "mis", "mul"}


def normalize(code: str | None) -> str | None:
    """Return the ISO 639-1 code for a 639-1/639-2 code, IETF tag or English name.

    Returns None for missing, undetermined or unknown languages.
    """
    if not code:
        return None
    c = code.strip().lower().replace("_", "-").split("-")[0]
    if c in _UNDETERMINED:
        return None
    if c in _ISO2:
        return c
    for iso1, iso2 in _ISO2.items():
        if c in iso2:
            return iso1
    return _NAMES.get(c)


def is_known(iso1: str) -> bool:
    return iso1 in _ISO2


def tesseract_model(iso1: str) -> str | None:
    """Tesseract model name for an ISO 639-1 code (``de`` -> ``deu``)."""
    if iso1 not in _ISO2:
        return None
    return _TESSERACT.get(iso1, _ISO2[iso1][-1])
