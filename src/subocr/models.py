"""Tesseract language models: English ships in the image, others download on demand.

At startup the models folder (TESSDATA_DIR, /config/tessdata in the image) is
seeded with the models baked into the image, then any model a configured
language needs is downloaded into it. Tesseract and subtile-ocr read models
from there through TESSDATA_PREFIX, so downloads survive image rebuilds.

Downloads come from the tessdata 4.1.0 release: the same models Debian's
tesseract-ocr-<lang> packages contain (tesseract-lang 4.1.0).
"""

from __future__ import annotations

import contextlib
import glob
import logging
import os
import secrets
import shutil
import urllib.request
from collections.abc import Iterable, Mapping
from pathlib import Path

from . import ocr
from .languages import tesseract_model

log = logging.getLogger(__name__)

DEFAULT_MODEL_URL = "https://github.com/tesseract-ocr/tessdata/raw/4.1.0/{model}.traineddata"
SYSTEM_TESSDATA_GLOB = "/usr/share/tesseract-ocr/*/tessdata"
MIN_MODEL_BYTES = 100_000  # real models are 1-30 MB; anything smaller is an error page
TIMEOUT = 120


class ModelError(RuntimeError):
    pass


def _atomic_copy(src: Path, dest: Path) -> None:
    tmp = dest.parent / f".{dest.name}.{secrets.token_hex(4)}.tmp"
    try:
        shutil.copyfile(src, tmp)  # copyfile, not copy2: CIFS refuses chmod
        os.replace(tmp, dest)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


def download_model(url: str, dest: Path) -> int:
    """Download one model to `dest` atomically. Returns its size in bytes."""
    tmp = dest.parent / f".{dest.name}.{secrets.token_hex(4)}.tmp"
    req = urllib.request.Request(url, headers={"User-Agent": "subocr"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp, open(tmp, "wb") as out:  # noqa: S310
            shutil.copyfileobj(resp, out, length=1 << 20)
        size = tmp.stat().st_size
        with open(tmp, "rb") as f:
            head = f.read(1)
        if size < MIN_MODEL_BYTES or head in (b"<", b"{"):
            raise ModelError(f"{url} returned {size} bytes, not a model")
        os.replace(tmp, dest)
        return size
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


def ensure_models(
    languages: Iterable[str],
    tessdata_dir: Path,
    download: bool = True,
    url_template: str = DEFAULT_MODEL_URL,
    system_dirs: Iterable[str] | None = None,
) -> dict[str, str]:
    """Make the models for `languages` available in `tessdata_dir` and point Tesseract at it.

    Returns {model: "image" | "present" | "downloaded" | "missing"}. Never raises for a
    failed download: that language simply reports bitmap-no-model until it succeeds.
    """
    tessdata_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, str] = {}

    dirs = list(system_dirs) if system_dirs is not None else glob.glob(SYSTEM_TESSDATA_GLOB)
    for sysdir in dirs:
        for sub in ("configs", "tessconfigs"):  # small option files some Tesseract flags use
            src_dir, dest_dir = Path(sysdir) / sub, tessdata_dir / sub
            if src_dir.is_dir() and not dest_dir.exists():
                shutil.copytree(src_dir, dest_dir, copy_function=shutil.copyfile)
        for src in Path(sysdir).glob("*.traineddata"):
            dest = tessdata_dir / src.name
            if not dest.exists() or dest.stat().st_size != src.stat().st_size:
                _atomic_copy(src, dest)
            report[src.stem] = "image"

    os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)

    for lang in languages:
        model = tesseract_model(lang)
        if model is None or model in report:
            continue
        dest = tessdata_dir / f"{model}.traineddata"
        if dest.exists() and dest.stat().st_size >= MIN_MODEL_BYTES:
            report[model] = "present"
            continue
        if not download:
            log.warning("OCR model %s for %s is not installed and downloads are off "
                        "(OCR_MODEL_DOWNLOAD=false)", model, lang)
            report[model] = "missing"
            continue
        url = url_template.format(model=model)
        try:
            size = download_model(url, dest)
        except Exception as exc:
            log.warning("could not download OCR model %s for %s from %s: %s",
                        model, lang, url, exc)
            report[model] = "missing"
            continue
        log.info("downloaded OCR model %s for %s (%.1f MB)", model, lang, size / 1e6)
        report[model] = "downloaded"

    ocr.installed_models.cache_clear()
    return report


def prepare_from_env(languages: Iterable[str], env: Mapping[str, str] | None = None) -> None:
    """Run ensure_models when TESSDATA_DIR is set (it is in the image); otherwise do nothing."""
    env = os.environ if env is None else env
    tessdata_dir = env.get("TESSDATA_DIR")
    if not tessdata_dir:
        return
    download = env.get("OCR_MODEL_DOWNLOAD", "true").strip().lower() not in (
        "0", "false", "no", "off")
    report = ensure_models(
        languages,
        Path(tessdata_dir),
        download=download,
        url_template=env.get("OCR_MODEL_URL") or DEFAULT_MODEL_URL,
    )
    missing = sorted(m for m, status in report.items() if status == "missing")
    if missing:
        log.warning("OCR unavailable for models %s; those languages report bitmap-no-model",
                    ", ".join(missing))
