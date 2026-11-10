"""OCR bitmap subtitles to SRT with subtile-ocr (Tesseract)."""

from __future__ import annotations

import functools
import subprocess
from pathlib import Path


class OcrError(RuntimeError):
    pass


@functools.lru_cache(maxsize=1)
def installed_models() -> frozenset[str]:
    """Tesseract language models available on this machine."""
    try:
        proc = subprocess.run(
            ["tesseract", "--list-langs"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return frozenset()
    lines = (proc.stdout or proc.stderr).splitlines()
    # First line is a header: 'List of available languages in "...":'
    return frozenset(line.strip() for line in lines[1:] if line.strip())


def run_ocr(source: Path, model: str, output: Path, blacklist: str = "") -> None:
    """OCR a .sup (PGS) or .idx (VobSub) file to `output` (SRT)."""
    cmd = ["subtile-ocr", "-l", model, "-o", str(output)]
    if blacklist:
        cmd += ["-c", f"tessedit_char_blacklist={blacklist}"]
    cmd.append(str(source))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise OcrError("subtile-ocr not found on PATH") from exc
    if proc.returncode != 0 or not output.exists():
        detail = (proc.stderr or proc.stdout).strip()[-500:]
        raise OcrError(f"subtile-ocr failed with exit code {proc.returncode}: {detail}")
