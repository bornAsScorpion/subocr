"""Command-line interface.

    subocr probe FILE            show tracks and what would be produced
    subocr process FILE          produce the missing sidecars
    subocr models                list installed Tesseract models
    subocr cleanup-preview FILE  show which external subtitles cleanup would keep or archive
    subocr serve                 run the service: API, webhooks, worker, nightly scan
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__
from .config import TEXT_MODES, Config, ConfigError
from .extract import ExtractError
from .job import JobResult, process_file
from .models import prepare_from_env
from .ocr import installed_models
from .probe import MediaInfo, ProbeError, probe_media
from .sidecars import plan_cleanup


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="subocr", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=f"subocr {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("file", type=Path, help="video file")
        sp.add_argument("--lang", help="override LANGUAGES, e.g. en,ru")
        sp.add_argument("--text-mode", choices=TEXT_MODES, help="override TEXT_MODE")
        sp.add_argument("--json", action="store_true", help="machine-readable output")

    probe = sub.add_parser("probe", help="show tracks and the plan; writes nothing")
    add_common(probe)

    proc = sub.add_parser("process", help="produce missing sidecars for one file")
    add_common(proc)
    proc.add_argument("--dry-run", action="store_true", help="plan only, write nothing")
    proc.add_argument(
        "--output-dir", type=Path,
        help="write sidecars here instead of next to the video (for testing on read-only mounts)",
    )
    proc.add_argument(
        "--ignore-existing", action="store_true",
        help="with --output-dir: ignore sidecars next to the video, check only the output folder",
    )

    sub.add_parser("models", help="list installed Tesseract models")
    prev = sub.add_parser("cleanup-preview",
                          help="show which external subtitles cleanup would keep or archive")
    prev.add_argument("file", type=Path, help="video file")
    prev.add_argument("--lang", help="override LANGUAGES, e.g. en,ru")
    prev.add_argument("--json", action="store_true", help="machine-readable output")
    sub.add_parser("serve", help="run the service (API, webhooks, worker, nightly scan)")
    return p


def _flags(t) -> str:
    flags = [name for name, on in (("forced", t.forced), ("default", t.default),
                                   ("hi", t.hearing_impaired), ("comment", t.comment)) if on]
    return ",".join(flags) or "-"


def _print_human(info: MediaInfo, result: JobResult) -> None:
    print(info.path.name)
    duration = f"{info.duration / 60:.0f} min" if info.duration else "unknown length"
    container = info.container or "unknown container"
    print(f"  {container}, {duration}, {len(info.tracks)} subtitle streams")
    for t in info.tracks:
        cues = f"~{t.cues} cues" if t.cues is not None else "? cues"
        title = f' "{t.title}"' if t.title else ""
        decision = result.decisions.get(t.index, "")
        print(f"  #{t.index:<3} {t.codec:<18} {t.raw_language or '-':<4} {_flags(t):<14} "
              f"{cues:<10}{title}")
        if decision:
            print(f"         -> {decision}")
    for note in result.notes:
        print(f"  note: {note}")
    if result.targets:
        print("  targets:")
    for r in result.targets:
        extra = f" quality={r.quality}" if r.quality is not None else ""
        detail = f" ({r.detail})" if r.detail else ""
        head = f"{r.language} {r.variant:<6} #{r.stream:<3} {r.kind:<6}"
        print(f"    {head} {r.status}{extra}{detail}")
        if r.path:
            print(f"           {r.path}")
    print(f"  outcome: {result.outcome}")


def _cleanup_preview(args, cfg: Config) -> int:
    import os

    if not args.file.is_file():
        print(f"not a file: {args.file}", file=sys.stderr)
        return 2
    mode = os.environ.get("CLEANUP_MODE", "dry-run").strip().lower()
    archive_unknown = os.environ.get("ARCHIVE_UNKNOWN", "").strip().lower() in (
        "1", "true", "yes", "on")
    orphans = os.environ.get("ORPHAN_SUBS", "delete").strip().lower()
    plan = plan_cleanup(args.file, cfg.languages, mode, archive_unknown, orphans)
    if args.json:
        print(json.dumps([a.to_dict() for a in plan], indent=2, ensure_ascii=False))
        return 0
    print(f"{args.file.name}: keep {','.join(cfg.languages)} (CLEANUP_MODE={mode})")
    if not plan:
        print("  no external subtitles")
    for a in plan:
        name = Path(a.path).name
        print(f"  {a.action:<8} {name}  ({a.reason})")  # orphans say so in the reason
    counts = {k: sum(1 for a in plan if a.action == k) for k in ("keep", "archive", "delete")}
    print(f"  total: keep {counts['keep']}, archive {counts['archive']}, delete {counts['delete']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    quiet = logging.INFO if args.command == "serve" else logging.WARNING
    logging.basicConfig(level=logging.DEBUG if args.verbose else quiet,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not args.verbose:
        logging.getLogger("subocr.models").setLevel(logging.INFO)  # show model downloads

    if args.command == "serve":
        from .service.runner import serve

        try:
            serve()
        except ConfigError as exc:
            print(f"config error: {exc}", file=sys.stderr)
            return 2
        return 0

    try:
        cfg = Config.from_env()
        if getattr(args, "lang", None):
            cfg = cfg.with_languages(args.lang)
        if getattr(args, "text_mode", None):
            cfg = replace(cfg, text_mode=args.text_mode)
        if getattr(args, "output_dir", None):
            args.output_dir.mkdir(parents=True, exist_ok=True)
            cfg = replace(cfg, output_dir=str(args.output_dir))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if cfg.ocr_enabled:
        prepare_from_env(cfg.languages)

    if args.command == "models":
        for model in sorted(installed_models()):
            print(model)
        return 0

    if args.command == "cleanup-preview":
        return _cleanup_preview(args, cfg)

    if not args.file.is_file():
        print(f"not a file: {args.file}", file=sys.stderr)
        return 2
    ignore_existing = getattr(args, "ignore_existing", False)
    if ignore_existing and not cfg.output_dir:
        print("--ignore-existing requires --output-dir", file=sys.stderr)
        return 2

    try:
        info = probe_media(args.file)
        dry_run = args.command == "probe" or args.dry_run
        result = process_file(args.file, cfg, dry_run=dry_run, info=info,
                              ignore_existing=ignore_existing)
    except (ProbeError, ExtractError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print_human(info, result)
    return 1 if result.outcome == "failed" else 0
