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


def _parse_log(out: str | None) -> list[dict[str, str]]:
    if not out:
        return []
    rows: list[dict[str, str]] = []
    for line in out.splitlines():
        if "\x1f" not in line:
            continue
        h, date, author, subject = (line.split("\x1f", 3) + ["", "", "", ""])[:4]
        rows.append({"hash": h, "date": date, "author": author, "subject": subject})
    return rows


def commits_with_dates(root: Path, n: int = 25) -> list[dict[str, str]]:
    out = _git(root, "log", f"-n{n}", "--no-merges", "--date=short",
               "--format=%h\x1f%ad\x1f%an\x1f%s")
    return _parse_log(out)


def file_history(root: Path, path: str, n: int = 25) -> list[dict[str, str]]:
    out = _git(root, "log", f"-n{n}", "--date=short",
               "--format=%h\x1f%ad\x1f%an\x1f%s", "--", path)
    return _parse_log(out)
