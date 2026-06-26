"""Optional semantic embeddings via fastembed (ONNX; no torch).

Disabled-by-default and fully optional: if fastembed/numpy aren't installed,
``get_embedder`` returns None and the system runs lexical-only. Vectors are
stored L2-normalized as float32 bytes so similarity is a plain dot product.

Search is a brute-force cosine scan in numpy — correct and simple at H1 scale
(tens of thousands of chunks). The upgrade path at larger scale is an ANN index
(e.g. hnswlib) over the same stored vectors; the storage format already supports
it (embeddings.vec is contiguous float32).
"""

from __future__ import annotations

import functools
import threading
from typing import Optional

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


def compose_symbol_text(
    kind: object, name: object, signature: object, docstring: object
) -> str:
    """The text embedded for a symbol: its kind, name, signature, and docstring —
    the distilled intent, far denser than a raw code body."""
    parts = [str(p) for p in (kind, name, signature) if p]
    text = " ".join(parts)
    if docstring:
        text += f": {docstring}"
    return text or (str(name) if name else "")


class Embedder:
    def __init__(self, model_name: str) -> None:
        from fastembed import TextEmbedding  # type: ignore

        self._model = TextEmbedding(model_name=model_name)
        self.model_name = model_name
        self._dim: Optional[int] = None

    @property
    def dim(self) -> int:
        if self._dim is None:
            vecs = self.embed(["dimension probe"])
            self._dim = len(vecs[0]) // 4  # float32 => 4 bytes each
        return self._dim

    def embed(self, texts: list[str]) -> list[bytes]:
        import numpy as np  # type: ignore

        out: list[bytes] = []
        for vec in self._model.embed(texts):
            arr = np.asarray(vec, dtype="float32")
            norm = float(np.linalg.norm(arr))
            if norm > 0:
                arr = arr / norm
            out.append(arr.astype("float32").tobytes())
        return out

    def embed_query(self, text: str) -> bytes:
        return self.embed([text])[0]


_EMBEDDER_LOCK = threading.Lock()


@functools.lru_cache(maxsize=4)
def _build_embedder(model_name: Optional[str]) -> Optional[Embedder]:
    try:
        import numpy  # noqa: F401  (required for storage/search)
        import fastembed  # noqa: F401
    except Exception:
        return None
    try:
        return Embedder(model_name or DEFAULT_MODEL)
    except Exception:
        return None


def get_embedder(model_name: Optional[str] = None) -> Optional[Embedder]:
    """Return a (cached) Embedder, or None if the optional backend isn't installed.

    Cached so the long-lived MCP server loads the ONNX model once, not per query.
    The lock serializes construction so the background pre-warm thread and a
    concurrent first query can't both load the model (single load, not double).
    """
    with _EMBEDDER_LOCK:
        return _build_embedder(model_name)
