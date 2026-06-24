"""Best-effort git metadata for intent mining (all guarded, never raises)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional


def _git(root: Path, *args: str, timeout: int = 30) -> Optional[str]:
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, timeout=timeout, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.decode("utf-8", "replace").strip()


def head_sha(root: Path) -> Optional[str]:
    return _git(root, "rev-parse", "HEAD")


def recent_commit_subjects(root: Path, n: int = 30) -> list[str]:
    out = _git(root, "log", f"-n{n}", "--no-merges", "--format=%s")
    if not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]
