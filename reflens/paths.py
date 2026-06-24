"""Filesystem layout for reflens local state.

Default base: ``~/.reflens`` (override with ``REFLENS_HOME``).

    <home>/
      repos/
        <name>/
          index.db          # SQLite: files, symbols, chunks, edges, FTS5, meta
          blobs/            # content-addressed gzip originals (Tier 2, lossless)
            ab/cd/<sha256>.gz

Repo names are validated to a strict slug so a name can never escape the repos
directory (path-traversal guard) and is safe as a directory and SQLite value.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_NAME_RE = re.compile(r"[^a-z0-9._-]+")
_MAX_NAME_LEN = 100


class InvalidRepoName(ValueError):
    """Raised when a repo name cannot be reduced to a safe slug."""


def home_dir() -> Path:
    env = os.environ.get("REFLENS_HOME", "").strip()
    base = Path(env).expanduser() if env else Path.home() / ".reflens"
    return base


def repos_dir() -> Path:
    return home_dir() / "repos"


def slugify_name(raw: str) -> str:
    """Reduce an arbitrary string to a safe repo slug.

    Lowercases, replaces runs of disallowed chars with ``-``, strips leading
    dots/dashes (so the result is never hidden or relative), and bounds length.
    """
    s = raw.strip().lower()
    s = _NAME_RE.sub("-", s)
    s = s.strip("._-")
    s = s[:_MAX_NAME_LEN].strip("._-")
    if not s or s in (".", ".."):
        raise InvalidRepoName(f"cannot derive a safe repo name from {raw!r}")
    return s


def repo_dir(name: str) -> Path:
    """Return the directory for a repo, asserting it stays under repos_dir().

    ``name`` must already be a slug (callers should pass ``slugify_name`` output).
    The realpath containment check is a defense-in-depth guard.
    """
    base = repos_dir()
    d = (base / name).resolve()
    base_resolved = base.resolve()
    if base_resolved != d and base_resolved not in d.parents:
        raise InvalidRepoName(f"repo path escapes base: {name!r}")
    return d


def db_path(name: str) -> Path:
    return repo_dir(name) / "index.db"


def blobs_dir(name: str) -> Path:
    return repo_dir(name) / "blobs"


def ensure_repo_dirs(name: str) -> Path:
    d = repo_dir(name)
    (d / "blobs").mkdir(parents=True, exist_ok=True)
    return d
