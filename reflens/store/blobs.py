"""Content-addressed, gzip-compressed blob store — the lossless Tier-2 backing.

Every file's *exact original bytes* are stored here, keyed by SHA-256. This is
what makes "no context is lost" a provable property rather than a claim:

  - put(data) is idempotent and content-addressed: identical bytes are stored once.
  - get(sha) decompresses AND re-verifies the SHA-256 before returning, so silent
    corruption is impossible to read past.
  - bytes in == bytes out, for any encoding or binary content.

Layout: ``<root>/ab/cd/<sha256>.gz`` (2-level sharding keeps directories small).
"""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path


class BlobMissing(KeyError):
    """Requested blob hash is not present in the store."""


class BlobCorrupt(RuntimeError):
    """Stored blob failed SHA-256 verification on read."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path_for(self, sha: str) -> Path:
        if len(sha) != 64 or not all(c in "0123456789abcdef" for c in sha):
            raise ValueError(f"not a sha256 hex digest: {sha!r}")
        return self.root / sha[:2] / sha[2:4] / f"{sha}.gz"

    def has(self, sha: str) -> bool:
        return self._path_for(sha).exists()

    def put(self, data: bytes) -> str:
        """Store bytes, return their SHA-256. Idempotent.

        Writes to a temp file then atomically renames, so a crash mid-write can
        never leave a half-written blob that would later fail verification.
        """
        sha = sha256_hex(data)
        dest = self._path_for(sha)
        if dest.exists():
            return sha
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".gz.tmp")
        # mtime=0 => byte-stable archives (reproducible store, content-addressable).
        tmp.write_bytes(gzip.compress(data, compresslevel=6, mtime=0))
        tmp.replace(dest)
        return sha

    def get(self, sha: str) -> bytes:
        path = self._path_for(sha)
        if not path.exists():
            raise BlobMissing(sha)
        raw = gzip.decompress(path.read_bytes())
        actual = sha256_hex(raw)
        if actual != sha:
            raise BlobCorrupt(f"blob {sha} decompressed to {actual}")
        return raw

    def get_text(self, sha: str, encoding: str = "utf-8") -> str:
        return self.get(sha).decode(encoding, errors="replace")
