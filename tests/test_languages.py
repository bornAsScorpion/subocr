import pytest

from subocr.languages import normalize, tesseract_model


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("en", "en"), ("eng", "en"), ("ENG", "en"), ("en-US", "en"),
        ("ger", "de"), ("deu", "de"), ("fre", "fr"), ("fra", "fr"),
        ("rus", "ru"), ("hun", "hu"), ("English", "en"), ("Russian", "ru"),
        ("und", None), ("", None), (None, None), ("xx", None),
    ],
)
def test_normalize(code, expected):
    assert normalize(code) == expected


def test_tesseract_model():
    assert tesseract_model("en") == "eng"
    assert tesseract_model("de") == "deu"
    assert tesseract_model("fr") == "fra"
    assert tesseract_model("ru") == "rus"
    assert tesseract_model("zz") is None
