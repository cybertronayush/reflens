"""Streaming parser for Repomix markdown dumps.

Format (markdown style):
    ## File: path/to/file.ext
    ````<lang?>
    <content>
    ````

Repomix picks a fence of >=3 backticks (this dump uses 4) so file content can
itself contain triple-backtick fences without colliding. We capture the exact
opening fence and close only on an identical run at column 0.

Caveat surfaced to the caller: if the dump was generated with transformations
(``--remove-comments``, ``--output-show-line-numbers``, ``--compress``), the
content here reflects those transforms. Losslessness is *relative to the dump*.
For byte-identical-to-source fidelity, ingest the directory instead.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

_FILE_HDR = "## File: "
_OPEN_FENCE = re.compile(r"^\s*(`{3,})\s*[\w.+-]*\s*$")

_TRANSFORM_HINTS = (
    ("line numbers have been added", "line_numbers"),
    ("comments have been removed", "comments_removed"),
    ("empty lines have been removed", "empty_lines_removed"),
    ("content has been compressed", "compressed"),
)


def looks_like_repomix(path: Path) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    return ("Repomix" in head or "packed representation" in head) and _FILE_HDR in (
        head + _peek_for_header(path)
    )


def _peek_for_header(path: Path) -> str:
    # Header may be past the first 4KB; scan a bit more, cheaply.
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(200_000)
    except OSError:
        return ""


def detect_transforms(path: Path) -> list[str]:
    """Report which lossy transforms the dump's preamble declares (for honesty)."""
    found: list[str] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096).lower()
    except OSError:
        return found
    for needle, tag in _TRANSFORM_HINTS:
        if needle in head:
            found.append(tag)
    return found


def iter_repomix(path: Path) -> Iterator[tuple[str, str]]:
    """Yield ``(repo_relative_path, content)`` for each file entry, streaming."""
    cur_path: str | None = None
    fence: str | None = None
    buf: list[str] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if cur_path is None:
                if line.startswith(_FILE_HDR):
                    cur_path = line[len(_FILE_HDR):].strip()
                    fence = None
                    buf = []
                continue
            if fence is None:
                if not line.strip():
                    continue  # tolerate a blank between header and fence
                m = _OPEN_FENCE.match(line)
                if m:
                    fence = m.group(1)
                    buf = []
                else:
                    # Header with no code block — skip this entry.
                    cur_path = None
                continue
            # inside content: close on an identical fence run
            if line.rstrip() == fence:
                yield (cur_path, "\n".join(buf))
                cur_path = None
                fence = None
                buf = []
            else:
                buf.append(line)
        # EOF inside an unterminated block: emit what we have (don't drop data).
        if cur_path is not None and fence is not None:
            yield (cur_path, "\n".join(buf))
