"""Walk a local directory or git repo, streaming includable files.

Git-aware: when the root is a git repo and ``git`` is available, file selection
uses ``git ls-files`` so .gitignore is respected for free and only real tracked
sources are seen. Otherwise falls back to ``os.walk`` with a built-in prune set.

Binary and oversized files are skipped from indexing (reported, not silently
dropped) — they aren't reasoning context. Use ``include_binary`` to override.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

DEFAULT_MAX_FILE_BYTES = 2_000_000

_PRUNE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    "dist", "build", "target", ".next", ".nuxt", ".cache", ".gradle",
    "vendor", ".idea", ".vscode", "coverage", ".terraform", ".reflens",
    ".serena", ".codegraph",
}


@dataclass
class WalkItem:
    path: str  # POSIX, repo-relative
    data: Optional[bytes]
    skipped: Optional[str]  # reason if skipped, else None


def _is_binary(data: bytes) -> bool:
    # git's own heuristic: a NUL byte in the head means binary.
    return b"\x00" in data[:8192]


def _git_tracked_files(root: Path) -> Optional[list[str]]:
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, timeout=60, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    parts = out.stdout.split(b"\x00")
    return [p.decode("utf-8", "replace") for p in parts if p]


def iter_dir(
    root: Path,
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    include_binary: bool = False,
) -> Iterator[WalkItem]:
    root = Path(root)
    tracked = _git_tracked_files(root)
    if tracked is not None:
        rel_paths = sorted(tracked)
        for rel in rel_paths:
            abs_p = root / rel
            yield _read_item(abs_p, rel, max_file_bytes, include_binary)
        return
    # Non-git fallback.
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS and not d.startswith(".git")]
        for fn in sorted(filenames):
            abs_p = Path(dirpath) / fn
            rel = abs_p.relative_to(root).as_posix()
            yield _read_item(abs_p, rel, max_file_bytes, include_binary)


def _read_item(abs_p: Path, rel: str, max_file_bytes: int, include_binary: bool) -> WalkItem:
    try:
        if not abs_p.is_file() or abs_p.is_symlink():
            return WalkItem(rel, None, "not-a-regular-file")
        size = abs_p.stat().st_size
        if size > max_file_bytes:
            return WalkItem(rel, None, f"too-large ({size} bytes)")
        data = abs_p.read_bytes()
    except OSError as exc:
        return WalkItem(rel, None, f"read-error: {exc}")
    if not include_binary and _is_binary(data):
        return WalkItem(rel, None, "binary")
    return WalkItem(rel, data, None)
