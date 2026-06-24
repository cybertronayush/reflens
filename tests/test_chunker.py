from __future__ import annotations

from reflens.ingest.chunker import chunk_text


def test_coverage_is_gap_free():
    text = "\n".join(f"line {i}" for i in range(1, 501))
    chunks = chunk_text(text, target_tokens=50, overlap_lines=5)
    assert chunks
    # Every line 1..500 must be covered by at least one chunk.
    covered = set()
    for c in chunks:
        for ln in range(c.start_line, c.end_line + 1):
            covered.add(ln)
    assert covered == set(range(1, 501))
    # ordered and forward-progressing
    assert chunks[0].start_line == 1
    assert chunks[-1].end_line == 500


def test_empty():
    assert chunk_text("") == []
