"""Process one video file: the core that the CLI and the service both call."""

from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import cleanup, extract, ocr
from .config import Config
from .languages import tesseract_model
from .naming import find_sidecars, satisfies, sidecar_name
from .probe import MediaInfo, probe_media
from .rules import FORCED, Plan, Target, build_plan
from .writer import write_no_clobber

# Per-target statuses
PLANNED = "planned"  # dry run: would be produced
WRITTEN = "written"
EXISTS = "exists"  # a sidecar already covers it (Bazarr, the plugin, a release sub)
LOST_RACE = "lost-race"  # a sidecar appeared between probe and write
LEFT_TO_PLUGIN = "left-to-plugin"
OCR_DISABLED = "ocr-disabled"
NO_MODEL = "bitmap-no-model"
UNSUPPORTED_CONTAINER = "unsupported-container"
FAILED = "failed"


@dataclass
class TargetResult:
    language: str
    variant: str
    stream: int
    kind: str  # "text" | "bitmap"
    status: str
    path: str | None = None
    quality: float | None = None
    detail: str = ""


@dataclass
class JobResult:
    path: str
    outcome: str
    targets: list[TargetResult] = field(default_factory=list)
    decisions: dict[int, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Work:
    target: Target
    result: TargetResult
    default: bool = False


def _outcome(plan: Plan, results: list[TargetResult]) -> str:
    statuses = {r.status for r in results}
    if FAILED in statuses:
        return "failed"
    if any(r.status == WRITTEN and r.kind == "bitmap" for r in results):
        return "ocr-done"
    if any(r.status == WRITTEN for r in results):
        return "text-extracted"
    if PLANNED in statuses:
        return "planned"
    if not plan.targets:
        if any(d.startswith("skip: untagged") for d in plan.decisions.values()):
            return "untagged-skipped"
        return "no-wanted-language"
    if NO_MODEL in statuses:
        return "bitmap-no-model"
    if statuses <= {EXISTS}:
        return "complete"
    if LEFT_TO_PLUGIN in statuses:
        return "left-to-plugin"
    return "nothing-to-do"


def _choose_default(work: list[_Work], cfg: Config, covered: set[str]) -> None:
    """Mark the top-ranked default language we are about to produce (DEFAULT_MARKING)."""
    if not cfg.default_marking:
        return
    for lang in cfg.default_languages:
        for w in work:
            if w.target.language == lang and w.target.variant != FORCED:
                w.default = True
                return
        if lang in covered:
            # An existing sub already serves the top-ranked language; don't promote a lower one
            return


def process_file(
    path: Path,
    cfg: Config,
    dry_run: bool = False,
    info: MediaInfo | None = None,
    ignore_existing: bool = False,
) -> JobResult:
    """Produce the missing sidecars for one video.

    ignore_existing (testing aid, needs cfg.output_dir): ignore sidecars next to the
    video and check only the output folder, so every wanted target is produced there.
    """
    path = Path(path)
    info = info or probe_media(path)
    plan = build_plan(info, cfg)
    out_dir = Path(cfg.output_dir) if cfg.output_dir else path.parent
    if ignore_existing and out_dir == path.parent:
        raise ValueError("ignore_existing requires an output_dir different from the video folder")
    extra_dirs = [out_dir] if out_dir != path.parent else []
    sidecars = find_sidecars(path, extra_dirs, include_video_dir=not ignore_existing)

    results: list[TargetResult] = []
    text_work: list[_Work] = []
    bitmap_work: list[_Work] = []
    needs_ocr = any(t.track.kind == "bitmap" for t in plan.targets)
    models = ocr.installed_models() if needs_ocr else frozenset()

    for target in plan.targets:
        t = target.track
        r = TargetResult(target.language, target.variant, t.index, t.kind, PLANNED)
        results.append(r)
        existing = next(
            (s for s in sidecars if satisfies(s, target.language, target.variant)), None
        )
        if existing:
            r.status, r.path = EXISTS, str(existing.path)
        elif t.kind == "text":
            if cfg.text_mode == "plugin":
                r.status = LEFT_TO_PLUGIN
            else:
                text_work.append(_Work(target, r))
        elif not cfg.ocr_enabled:
            r.status = OCR_DISABLED
        elif not info.is_matroska:
            r.status, r.detail = UNSUPPORTED_CONTAINER, info.container
        elif (model := tesseract_model(target.language)) not in models:
            r.status, r.detail = NO_MODEL, f"tesseract model {model} not installed"
        else:
            bitmap_work.append(_Work(target, r))

    covered = {s.language for s in sidecars if s.language and not s.forced}
    _choose_default(text_work + bitmap_work, cfg, covered)
    for w in text_work + bitmap_work:
        name = sidecar_name(path.stem, w.target.language, w.target.variant, w.default)
        w.result.path = str(out_dir / name)

    if not dry_run and (text_work or bitmap_work):
        scratch = Path(cfg.scratch_dir) if cfg.scratch_dir else None
        with tempfile.TemporaryDirectory(prefix="subocr-", dir=scratch) as tmp:
            work_dir = Path(tmp)
            _do_text(path, text_work, work_dir)
            _do_bitmap(path, bitmap_work, work_dir, cfg)

    return JobResult(
        path=str(path),
        outcome=_outcome(plan, results),
        targets=results,
        decisions=plan.decisions,
        notes=plan.notes,
    )


def _write(w: _Work, data: bytes) -> None:
    assert w.result.path
    try:
        written = write_no_clobber(Path(w.result.path), data)
    except OSError as exc:
        w.result.status = FAILED
        w.result.detail = f"cannot write sidecar in {Path(w.result.path).parent}: {exc}"
        return
    if written:
        w.result.status = WRITTEN
    else:
        w.result.status = LOST_RACE
        w.result.detail = "a sidecar appeared before the write; kept the existing file"


def _do_text(video: Path, work: list[_Work], work_dir: Path) -> None:
    if not work:
        return
    try:
        outputs = extract.extract_text(video, [w.target.track for w in work], work_dir)
    except extract.ExtractError as exc:
        for w in work:
            w.result.status, w.result.detail = FAILED, str(exc)
        return
    for w in work:
        src = outputs[w.target.track.index]
        if not src.exists() or src.stat().st_size == 0:
            w.result.status, w.result.detail = FAILED, "extracted track is empty"
            continue
        _write(w, src.read_bytes())


def _do_bitmap(video: Path, work: list[_Work], work_dir: Path, cfg: Config) -> None:
    if not work:
        return
    try:
        outputs = extract.extract_bitmap(video, [w.target.track for w in work], work_dir)
    except extract.ExtractError as exc:
        for w in work:
            w.result.status, w.result.detail = FAILED, str(exc)
        return
    for w in work:
        lang = w.target.language
        raw = work_dir / f"stream{w.target.track.index}.ocr.srt"
        try:
            ocr.run_ocr(outputs[w.target.track.index], tesseract_model(lang) or lang, raw,
                        cfg.ocr_blacklist)
            subs = cleanup.clean_subs(cleanup.load_srt(raw), lang)
        except (ocr.OcrError, OSError, ValueError) as exc:
            w.result.status, w.result.detail = FAILED, str(exc)
            continue
        if not subs.events:
            w.result.status, w.result.detail = FAILED, "OCR produced no cues"
            continue
        w.result.quality = cleanup.quality_score(subs, lang)
        _write(w, subs.to_string("srt").encode("utf-8"))
