import os

import pytest

from subocr import models


@pytest.fixture
def system_dir(tmp_path):
    d = tmp_path / "system"
    d.mkdir()
    (d / "eng.traineddata").write_bytes(b"E" * 200_000)
    (d / "osd.traineddata").write_bytes(b"O" * 150_000)
    (d / "configs").mkdir()
    (d / "configs" / "hocr").write_text("tessedit_create_hocr 1")
    return d


@pytest.fixture(autouse=True)
def restore_env(monkeypatch):
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)


def _fake_download(calls, size=300_000, body=b"M"):
    def download(url, dest):
        calls.append(url)
        dest.write_bytes(body * size)
        return size
    return download


def test_seeds_image_models_and_downloads_missing(tmp_path, system_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(models, "download_model", _fake_download(calls))
    target = tmp_path / "config" / "tessdata"
    report = models.ensure_models(["en", "ru", "de"], target, system_dirs=[str(system_dir)])
    assert report == {"eng": "image", "osd": "image", "rus": "downloaded", "deu": "downloaded"}
    assert sorted(p.name for p in target.iterdir()) == [
        "configs", "deu.traineddata", "eng.traineddata", "osd.traineddata", "rus.traineddata"]
    assert (target / "configs" / "hocr").read_text() == "tessedit_create_hocr 1"
    assert calls == [models.DEFAULT_MODEL_URL.format(model="rus"),
                     models.DEFAULT_MODEL_URL.format(model="deu")]
    assert os.environ["TESSDATA_PREFIX"] == str(target)


def test_second_start_downloads_nothing(tmp_path, system_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(models, "download_model", _fake_download(calls))
    target = tmp_path / "tessdata"
    models.ensure_models(["en", "ru"], target, system_dirs=[str(system_dir)])
    calls.clear()
    report = models.ensure_models(["en", "ru"], target, system_dirs=[str(system_dir)])
    assert calls == []
    assert report["rus"] == "present"


def test_download_failure_is_reported_not_raised(tmp_path, system_dir, monkeypatch):
    def fail(url, dest):
        raise OSError("network unreachable")

    monkeypatch.setattr(models, "download_model", fail)
    report = models.ensure_models(["en", "ru"], tmp_path / "t", system_dirs=[str(system_dir)])
    assert report["rus"] == "missing" and report["eng"] == "image"


def test_downloads_off(tmp_path, system_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(models, "download_model", _fake_download(calls))
    report = models.ensure_models(["ru"], tmp_path / "t", download=False,
                                  system_dirs=[str(system_dir)])
    assert report["rus"] == "missing" and calls == []


def test_download_model_rejects_error_pages(tmp_path, monkeypatch):
    import io
    import urllib.request

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout: Resp(b'{"message": "Not Found"}'))
    dest = tmp_path / "rus.traineddata"
    with pytest.raises(models.ModelError):
        models.download_model("https://example/rus.traineddata", dest)
    assert list(tmp_path.iterdir()) == []  # no partial file left

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout: Resp(b"\x00" * 200_000))
    assert models.download_model("https://example/rus.traineddata", dest) == 200_000
    assert [p.name for p in tmp_path.iterdir()] == ["rus.traineddata"]


def test_prepare_from_env_only_with_tessdata_dir(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(models, "ensure_models", lambda *a, **k: called.append(k) or {})
    models.prepare_from_env(["en"], env={})
    assert called == []
    models.prepare_from_env(["en"], env={"TESSDATA_DIR": str(tmp_path),
                                         "OCR_MODEL_DOWNLOAD": "false",
                                         "OCR_MODEL_URL": "https://mirror/{model}.traineddata"})
    assert called == [{"download": False,
                       "url_template": "https://mirror/{model}.traineddata"}]
