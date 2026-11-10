"""Job planning without external tools (dry runs on synthetic probe data)."""

from dataclasses import replace

import pytest

from subocr import job
from subocr.config import Config


@pytest.fixture(autouse=True)
def eng_model(monkeypatch):
    monkeypatch.setattr(job.ocr, "installed_models", lambda: frozenset({"eng", "rus"}))


def _place(info, tmp_path):
    video = tmp_path / info.path.name
    video.touch()
    return video, replace(info, path=video)


def test_vobsub_dry_run_plans_ocr(harbor, tmp_path):
    video, info = _place(harbor, tmp_path)
    result = job.process_file(video, Config(), dry_run=True, info=info)
    assert result.outcome == "planned"
    assert [(r.variant, r.status, r.kind) for r in result.targets] == [
        ("full", "planned", "bitmap"), ("forced", "planned", "bitmap")]
    assert result.targets[0].path.endswith("(1080p BluRay).en.srt")
    assert result.targets[1].path.endswith("(1080p BluRay).en.forced.srt")


def test_existing_sidecar_respected(harbor, tmp_path):
    video, info = _place(harbor, tmp_path)
    (tmp_path / f"{video.stem}.en.srt").touch()  # Bazarr got there first
    result = job.process_file(video, Config(), dry_run=True, info=info)
    assert [(r.variant, r.status) for r in result.targets] == [
        ("full", "exists"), ("forced", "planned")]


def test_complete_when_everything_exists(harbor, tmp_path):
    video, info = _place(harbor, tmp_path)
    (tmp_path / f"{video.stem}.en.srt").touch()
    (tmp_path / f"{video.stem}.en.forced.srt").touch()
    assert job.process_file(video, Config(), dry_run=True, info=info).outcome == "complete"


def test_plugin_mode_leaves_text_alone(courier, tmp_path):
    video, info = _place(courier, tmp_path)
    result = job.process_file(video, Config(text_mode="plugin"), dry_run=True, info=info)
    assert [r.status for r in result.targets] == ["left-to-plugin"]


def test_missing_model(harbor, tmp_path, monkeypatch):
    monkeypatch.setattr(job.ocr, "installed_models", lambda: frozenset({"rus"}))
    video, info = _place(harbor, tmp_path)
    result = job.process_file(video, Config(), dry_run=True, info=info)
    assert result.outcome == "bitmap-no-model"


def test_default_marking(courier, tmp_path):
    video, info = _place(courier, tmp_path)
    cfg = Config(languages=("de", "en"), default_languages=("en",), default_marking=True)
    result = job.process_file(video, cfg, dry_run=True, info=info)
    paths = {r.language: r.path for r in result.targets}
    assert paths["en"].endswith(".en.default.srt")
    assert paths["de"].endswith(".de.srt")


def test_default_not_promoted_when_top_language_already_covered(courier, tmp_path):
    video, info = _place(courier, tmp_path)
    (tmp_path / f"{video.stem}.en.srt").touch()
    cfg = Config(languages=("en", "de"), default_languages=("en", "de"), default_marking=True)
    result = job.process_file(video, cfg, dry_run=True, info=info)
    de = next(r for r in result.targets if r.language == "de")
    assert de.path.endswith(".de.srt")


def test_output_dir(harbor, tmp_path):
    video, info = _place(harbor, tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    result = job.process_file(video, Config(output_dir=str(out)), dry_run=True, info=info)
    assert all(r.path.startswith(str(out)) for r in result.targets)


def test_ignore_existing_checks_only_output_dir(harbor, tmp_path):
    video, info = _place(harbor, tmp_path)
    (tmp_path / f"{video.stem}.en.srt").touch()  # library sidecar, e.g. from Bazarr
    out = tmp_path / "out"
    out.mkdir()
    (out / f"{video.stem}.en.forced.srt").touch()  # already produced in an earlier test run
    cfg = Config(output_dir=str(out))
    result = job.process_file(video, cfg, dry_run=True, info=info, ignore_existing=True)
    assert [(r.variant, r.status) for r in result.targets] == [
        ("full", "planned"), ("forced", "exists")]
    assert result.targets[0].path.startswith(str(out))


def test_ignore_existing_needs_output_dir(harbor, tmp_path):
    video, info = _place(harbor, tmp_path)
    with pytest.raises(ValueError):
        job.process_file(video, Config(), dry_run=True, info=info, ignore_existing=True)


def test_config_from_env():
    cfg = Config.from_env({"LANGUAGES": "eng, ru", "HI_POLICY": "both", "OCR_ENABLED": "no"})
    assert cfg.languages == ("en", "ru")
    assert cfg.hi_policy == "both"
    assert cfg.ocr_enabled is False
