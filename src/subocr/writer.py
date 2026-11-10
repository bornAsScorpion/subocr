"""Atomic, no-clobber sidecar writes.

Works on CIFS/SMB mounts, which commonly refuse chmod and hard links: the temp
file is created with its final mode (0666 minus umask) so no chmod is needed, and
the hard-link step falls back to check-then-rename where links are unsupported.
"""

from __future__ import annotations

import contextlib
import os
import secrets
from pathlib import Path


def write_no_clobber(dest: Path, data: bytes) -> bool:
    """Write `data` to `dest` only if `dest` does not exist. Returns True if written.

    The data goes to a hidden temp file in the same folder first, so Jellyfin never
    sees a partial file, and the final step stays on one filesystem. That step is a
    hard link, which fails atomically if the target appeared meanwhile (Bazarr or
    the plugin won the race). Where hard links are unsupported it falls back to
    check-then-rename, which leaves a very small race window.
    """
    tmp = dest.parent / f".subocr-{secrets.token_hex(6)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            with contextlib.suppress(OSError):
                os.fsync(f.fileno())
        try:
            os.link(tmp, dest)
        except FileExistsError:
            return False
        except OSError:  # EPERM / ENOTSUP on CIFS, EXDEV, ...
            if dest.exists():
                return False
            os.rename(tmp, dest)
        return True
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
