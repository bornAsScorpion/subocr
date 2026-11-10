"""Pull subtitle streams out of the container into a scratch folder.

Each kind is extracted in one call per file, so the file is read once per kind:
text tracks with ffmpeg (converted to SRT), bitmap tracks with mkvextract
(PGS as .sup, VobSub as .idx + .sub; ffmpeg cannot write these).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .probe import Track


class ExtractError(RuntimeError):
    pass


def _run(cmd: list[str], ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise ExtractError(f"{cmd[0]} not found on PATH") from exc
    if proc.returncode not in ok_codes:
        detail = (proc.stderr or proc.stdout).strip()[-500:]
        raise ExtractError(f"{cmd[0]} failed with exit code {proc.returncode}: {detail}")
    return proc


def extract_text(video: Path, tracks: Sequence[Track], workdir: Path) -> dict[int, Path]:
    """Extract text tracks as SRT. Returns stream index -> SRT path."""
    if not tracks:
        return {}
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(video)]
    outputs: dict[int, Path] = {}
    for t in tracks:
        out = workdir / f"stream{t.index}.srt"
        cmd += ["-map", f"0:{t.index}", "-c:s", "srt", "-f", "srt", str(out)]
        outputs[t.index] = out
    _run(cmd)
    return outputs


def mkv_subtitle_ids(video: Path) -> list[int]:
    """mkvmerge track IDs of subtitle tracks, in container order."""
    # mkvmerge exits 1 on warnings, which is still a valid result
    proc = _run(["mkvmerge", "-J", str(video)], ok_codes=(0, 1))
    data = json.loads(proc.stdout or "{}")
    return [t["id"] for t in data.get("tracks", []) if t.get("type") == "subtitles"]


def extract_bitmap(video: Path, tracks: Sequence[Track], workdir: Path) -> dict[int, Path]:
    """Extract bitmap tracks (Matroska only). Returns stream index -> .sup / .idx path."""
    if not tracks:
        return {}
    ids = mkv_subtitle_ids(video)
    specs: list[str] = []
    outputs: dict[int, Path] = {}
    for t in tracks:
        if t.ordinal >= len(ids):
            raise ExtractError(f"no mkvmerge track for subtitle stream {t.index}")
        ext = ".sup" if t.codec == "hdmv_pgs_subtitle" else ".idx"
        out = workdir / f"stream{t.index}{ext}"
        specs.append(f"{ids[t.ordinal]}:{out}")
        outputs[t.index] = out
    _run(["mkvextract", str(video), "tracks", *specs], ok_codes=(0, 1))
    return outputs
