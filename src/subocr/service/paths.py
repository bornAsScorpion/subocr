"""Path mapping and library confinement for paths sent by callers."""

from __future__ import annotations

import os
import posixpath
from collections.abc import Iterable


class PathRejected(ValueError):
    pass


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


class PathPolicy:
    """Translate caller paths into local ones, and keep everything inside the libraries.

    PATH_MAPPINGS pairs are (remote prefix, local prefix), like the *arr stack's
    remote path mappings: Jellyfin sends /mnt/media/..., we see /data/...
    """

    def __init__(self, libraries: Iterable[str], mappings: Iterable[tuple[str, str]] = ()):
        self.libraries = tuple(posixpath.normpath(p) for p in libraries)
        maps = [(posixpath.normpath(r), posixpath.normpath(loc)) for r, loc in mappings]
        self._in = sorted(maps, key=lambda m: -len(m[0]))
        self._out = sorted(((loc, r) for r, loc in maps), key=lambda m: -len(m[0]))

    @staticmethod
    def _apply(path: str, table: list[tuple[str, str]]) -> str:
        for src, dst in table:
            if path == src:
                return dst
            if _under(path, src):
                return posixpath.join(dst, path[len(src.rstrip("/")) + 1 :])
        return path

    def to_local(self, path: str) -> str:
        return self._apply(posixpath.normpath(path), self._in)

    def to_remote(self, local_path: str) -> str:
        """Local path as the remote side (e.g. Jellyfin) sees it."""
        return self._apply(posixpath.normpath(local_path), self._out)

    def check(self, local_path: str) -> str:
        if not local_path.startswith("/"):
            raise PathRejected(f"path must be absolute: {local_path!r}")
        normalized = posixpath.normpath(local_path)
        real = os.path.realpath(normalized)
        roots = [(lib, os.path.realpath(lib)) for lib in self.libraries]
        if not any(_under(normalized, lib) and _under(real, real_lib) for lib, real_lib in roots):
            raise PathRejected(f"path is outside LIBRARY_PATHS: {local_path!r}")
        return normalized

    def resolve(self, path: str) -> str:
        """Map a caller's path to local and confirm it is inside a library."""
        return self.check(self.to_local(path))
