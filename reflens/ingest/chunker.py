"""Gap-free, line-aligned chunking for retrieval.

Chunks tile the file with a small overlap. Coverage is total (every line is in
at least one chunk), so the chunk set is a lossless cover of the file — and the
byte-perfect original still lives in Tier 2 regardless.
"""

from __future__ import annotations

from ..models import Chunk
from ..tokenizer import estimate_tokens

DEFAULT_TARGET_TOKENS = 400
DEFAULT_OVERLAP_LINES = 10
_HARD_LINE_CAP = 600  # never let a single chunk exceed this many lines


def chunk_text(
    text: str,
    *,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_lines: int = DEFAULT_OVERLAP_LINES,
) -> list[Chunk]:
    if not text:
        return []
    lines = text.split("\n")
    n = len(lines)
    chunks: list[Chunk] = []
    start = 0  # 0-indexed line cursor
    ordn = 0
    while start < n:
        acc_tokens = 0
        end = start  # exclusive cursor
        while end < n:
            acc_tokens += estimate_tokens(lines[end]) + 1
            end += 1
            if acc_tokens >= target_tokens and (end - start) >= 1:
                break
            if (end - start) >= _HARD_LINE_CAP:
                break
        body = "\n".join(lines[start:end])
        chunks.append(
            Chunk(
                ord=ordn,
                start_line=start + 1,
                end_line=end,  # 1-indexed inclusive == 0-indexed exclusive
                text=body,
                token_est=estimate_tokens(body),
            )
        )
        ordn += 1
        if end >= n:
            break
        # advance with overlap, but always make forward progress
        nxt = end - overlap_lines
        start = nxt if nxt > start else end
    return chunks
