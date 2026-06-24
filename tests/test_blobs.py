from __future__ import annotations

import gzip

import pytest

from reflens.store.blobs import BlobCorrupt, BlobMissing, BlobStore, sha256_hex


def test_roundtrip_all_byte_values(tmp_path):
    bs = BlobStore(tmp_path / "blobs")
    samples = [b"hello", "café \U0001f600".encode(), bytes(range(256)), b""]
    for s in samples:
        sha = bs.put(s)
        assert bs.get(sha) == s


def test_idempotent(tmp_path):
    bs = BlobStore(tmp_path / "blobs")
    a = bs.put(b"same")
    b = bs.put(b"same")
    assert a == b == sha256_hex(b"same")


def test_missing_raises(tmp_path):
    bs = BlobStore(tmp_path / "blobs")
    with pytest.raises(BlobMissing):
        bs.get("0" * 64)


def test_corruption_detected(tmp_path):
    bs = BlobStore(tmp_path / "blobs")
    sha = bs.put(b"original")
    # Tamper: overwrite the stored gz with different content under the same name.
    path = bs._path_for(sha)
    path.write_bytes(gzip.compress(b"tampered", mtime=0))
    with pytest.raises(BlobCorrupt):
        bs.get(sha)


def test_bad_hash_rejected(tmp_path):
    bs = BlobStore(tmp_path / "blobs")
    with pytest.raises(ValueError):
        bs.get("not-a-sha")
