"""Property/fuzz tests for the crux: the Tier-2 store loses nothing, ever.

reflens's entire honesty claim rests on byte-exact storage. These tests throw
adversarial input at the blob layer — random bytes across sizes, pathological
content, arbitrary unicode — and assert exact round-trips, content-addressing,
and corruption detection. Uses stdlib `random` (seeded) rather than hypothesis
to keep the test suite dependency-free, matching the project's zero-deps ethos.
"""

from __future__ import annotations

import random

import pytest

from reflens.engine import Repo
from reflens.ingest import ingest_source
from reflens.store import BlobCorrupt, BlobMissing, BlobStore

SEED = 1729  # fixed => deterministic, reproducible failures


def _rng() -> random.Random:
    return random.Random(SEED)


# Sizes that exercise empty, single-byte, gzip-buffer boundaries, and "large".
_SIZES = [0, 1, 2, 7, 255, 256, 257, 1023, 1024, 1025, 4096, 65535, 65536, 262144]


def test_blob_roundtrip_random_bytes(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    rng = _rng()
    for size in _SIZES:
        for _ in range(8):
            data = bytes(rng.getrandbits(8) for _ in range(size))
            sha = store.put(data)
            assert store.get(sha) == data, f"round-trip failed at size={size}"


def test_blob_roundtrip_pathological(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    cases = [
        b"",                          # empty
        b"\x00",                      # lone null
        b"\x00" * 100_000,            # highly compressible
        b"\xff" * 50_000,             # all-ones
        b"\r\n" * 10_000,             # CRLF storm
        b"\xef\xbb\xbfwith bom",      # UTF-8 BOM
        b"\xc3\x28",                  # invalid UTF-8 sequence
        b"\xed\xa0\x80",             # encoded surrogate (illegal in strict UTF-8)
        bytes(range(256)) * 500,      # every byte value
        b"a\x00b\x00c\x00",          # embedded nulls between text
    ]
    for data in cases:
        sha = store.put(data)
        assert store.get(sha) == data


def test_blob_roundtrip_arbitrary_unicode(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    rng = _rng()
    # Pull from planes incl. emoji, CJK, combining marks, RTL; skip surrogates
    # (not representable in str) — those are covered as raw bytes above.
    def rand_char() -> str:
        while True:
            cp = rng.randint(0, 0x10FFFF)
            if 0xD800 <= cp <= 0xDFFF:
                continue
            return chr(cp)

    for _ in range(200):
        text = "".join(rand_char() for _ in range(rng.randint(0, 64)))
        data = text.encode("utf-8")
        sha = store.put(data)
        assert store.get(sha).decode("utf-8") == text


def test_content_addressing_and_idempotency(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    payload = b"identical content"
    sha1 = store.put(payload)
    sha2 = store.put(payload)  # idempotent: same bytes -> same address
    assert sha1 == sha2
    assert store.put(b"different content") != sha1
    # one logical blob on disk despite two puts
    files = list((tmp_path / "blobs").rglob("*.gz"))
    assert len(files) == 2  # the payload + the "different content"


def test_corruption_is_detected(tmp_path):
    """Silent bit-rot must be caught: a blob decompressing to the wrong bytes
    (even if still valid gzip) fails the SHA check rather than returning garbage."""
    import gzip

    store = BlobStore(tmp_path / "blobs")
    sha = store.put(b"trust but verify")
    blob_path = next((tmp_path / "blobs").rglob("*.gz"))
    # Replace with VALID gzip of different content, addressed under the old sha.
    blob_path.write_bytes(gzip.compress(b"tampered payload", mtime=0))
    with pytest.raises(BlobCorrupt):
        store.get(sha)


def test_truncated_archive_raises(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    sha = store.put(b"some content here")
    blob_path = next((tmp_path / "blobs").rglob("*.gz"))
    raw = blob_path.read_bytes()
    blob_path.write_bytes(raw[: len(raw) // 2])  # half a gzip stream
    with pytest.raises((BlobCorrupt, OSError, EOFError)):
        store.get(sha)


def test_missing_blob_raises(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    with pytest.raises(BlobMissing):
        store.get("0" * 64)


def test_ingest_verify_on_fuzzed_repo(tmp_path):
    """End-to-end: a repo of varied/pathological files ingests and verifies."""
    rng = _rng()
    root = tmp_path / "fuzzrepo"
    (root / "sub").mkdir(parents=True)
    written: dict[str, bytes] = {}
    for i in range(25):
        ext = rng.choice([".py", ".txt", ".md", ".bin", ".json"])
        name = f"sub/f{i}{ext}" if i % 3 == 0 else f"f{i}{ext}"
        if ext == ".bin":
            data = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 8192)))
        else:
            chars = "".join(
                chr(rng.choice([rng.randint(32, 126), rng.randint(0x80, 0x2FFF), 0x1F600]))
                for _ in range(rng.randint(0, 2000))
            )
            data = chars.encode("utf-8")
        (root / name).write_bytes(data)
        written[name] = data

    ingest_source("fuzz", str(root))
    with Repo.open("fuzz") as r:
        res = r.verify()
        assert res["ok"] is True, res
        assert res["failed"] == []
        # every ingested file's stored blob equals the exact source bytes
        for row in r.db.list_files():
            assert r.blobs.get(row["sha256"]) == written[row["path"]]
