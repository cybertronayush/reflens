"""Token estimation for digest budgeting.

Uses tiktoken's ``cl100k_base`` when installed (accurate for OpenAI/Anthropic-ish
tokenization); otherwise a deterministic heuristic. The budgeter always keeps a
safety margin, so the heuristic being approximate is acceptable.
"""

from __future__ import annotations

import functools
import math

_HEURISTIC_CHARS_PER_TOKEN = 4.0


@functools.lru_cache(maxsize=1)
def _encoder():
    try:
        import tiktoken  # type: ignore

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def estimate_tokens(text: str) -> int:
    """Best-effort token count. Never raises; falls back to chars/4."""
    if not text:
        return 0
    enc = _encoder()
    if enc is not None:
        try:
            return len(enc.encode(text, disallowed_special=()))
        except Exception:
            pass
    return math.ceil(len(text) / _HEURISTIC_CHARS_PER_TOKEN)


def is_accurate() -> bool:
    """True when a real tokenizer (tiktoken) backs estimates."""
    return _encoder() is not None
