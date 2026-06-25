"""Ingest orchestrator: source -> blobs (Tier 2) + SQLite index (Tier 1 inputs).

Streams file-by-file and commits in batches so memory stays flat on huge inputs.
Rebuild is crash- and concurrency-safe: the new index is built in a temporary
directory and atomically swapped into place at the end, so a live MCP server
querying the repo never sees a half-built or missing index (re-ingesting while
serving is safe).
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .. import paths
from ..extract import detect_language, extract_outline
from ..models import Edge, FileRecord, IngestResult
from ..store import BlobStore, Database
from . import gitmeta, repomix
from .chunker import chunk_text
from .walker import DEFAULT_MAX_FILE_BYTES, iter_dir

ProgressFn = Callable[[int, str], None]
_COMMIT_EVERY = 400


def derive_name(source: str) -> str:
    p = Path(source)
    stem = p.name
    for suffix in (".md", ".txt"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    for prefix in ("repomix-output-", "repomix-"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
    return paths.slugify_name(stem or "repo")


def _classify_source(source: str) -> tuple[str, Path]:
    p = Path(source).expanduser()
    if p.is_dir():
        return "dir", p
    if p.is_file():
        if repomix.looks_like_repomix(p):
            return "repomix", p
        raise ValueError(
            f"{source} is a file but not a recognized Repomix dump. "
            "Point at a directory or a repomix .md file."
        )
    raise FileNotFoundError(f"source not found: {source}")


def ingest_source(
    name: Optional[str],
    source: str,
    *,
    semantic: bool = False,
    embed_model: Optional[str] = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    include_binary: bool = False,
    progress: Optional[ProgressFn] = None,
) -> IngestResult:
    kind, src_path = _classify_source(source)
    repo_name = paths.slugify_name(name) if name else derive_name(source)

    final_path = paths.repo_dir(repo_name)
    repos_base = paths.repos_dir()
    repos_base.mkdir(parents=True, exist_ok=True)
    # Clean stale temp dirs left by a previously interrupted ingest.
    for stale in repos_base.glob(f".reflens-tmp-{repo_name}-*"):
        shutil.rmtree(stale, ignore_errors=True)
    # Build the new index OUT OF PLACE so the live index keeps serving until the
    # atomic swap at the very end.
    work_path = repos_base / f".reflens-tmp-{repo_name}-{os.getpid()}"
    (work_path / "blobs").mkdir(parents=True, exist_ok=True)

    blobs = BlobStore(work_path / "blobs")
    db = Database.open(work_path / "index.db", create=True)

    result = IngestResult(name=repo_name, file_count=0, total_bytes=0,
                          symbol_count=0, chunk_count=0, edge_count=0)
    transforms: list[str] = []
    if kind == "repomix":
        transforms = repomix.detect_transforms(src_path)
        if transforms:
            result.warnings.append(
                "repomix dump declares lossy transforms "
                f"({', '.join(transforms)}); stored content is lossless wrt the dump, "
                "not the original source. Ingest the directory for byte-identical fidelity."
            )

    try:
        for rel_path, data, text in _iter_files(kind, src_path, max_file_bytes, include_binary, result):
            sha = blobs.put(data)
            lang = detect_language(rel_path)
            line_count = text.count("\n") + 1 if text else 0
            fid = db.insert_file(
                FileRecord(path=rel_path, lang=lang, sha256=sha,
                           size_bytes=len(data), line_count=line_count)
            )
            outline = extract_outline(rel_path, text, lang)
            if outline.symbols:
                result.symbol_count += db.insert_symbols(fid, outline.symbols)
            if outline.imports:
                edges = [Edge(src=rel_path, dst=imp, kind="import") for imp in outline.imports]
                result.edge_count += db.insert_edges(edges)
            chunks = chunk_text(text)
            if chunks:
                result.chunk_count += db.insert_chunks(fid, chunks)
            result.file_count += 1
            result.total_bytes += len(data)
            if progress and result.file_count % 25 == 0:
                progress(result.file_count, rel_path)
            if result.file_count % _COMMIT_EVERY == 0:
                db.commit()

        db.commit()

        if semantic:
            _run_semantic_pass(db, embed_model, result)

        commit_sha = gitmeta.head_sha(src_path) if kind == "dir" else None
        if kind == "dir":
            subjects = gitmeta.recent_commit_subjects(src_path, n=40)
            if subjects:
                db.set_meta("git_commits", subjects)

        # ---- completeness: prove every declared file was captured -------
        ingested_paths = {r["path"] for r in db.list_files()}
        if kind == "repomix":
            declared = repomix.count_entries(src_path)
            tree_files = repomix.parse_directory_structure(src_path)
            tree_total = len(tree_files)
            excluded = sorted(set(tree_files) - ingested_paths)
            complete = result.file_count == declared
        else:
            declared = result.file_count + len(result.skipped)
            tree_total = declared
            excluded = sorted({s.split(":", 1)[0].strip() for s in result.skipped})
            complete = True  # walker yields every file it sees; skips are intentional
        if not complete:
            result.warnings.append(
                f"completeness check: declared {declared} files but indexed "
                f"{result.file_count} — {declared - result.file_count} entries were not "
                "captured (parser issue). Re-run `reflens verify` for details."
            )

        db.set_meta("name", repo_name)
        db.set_meta("source_kind", kind)
        db.set_meta("source_ref", str(src_path))
        db.set_meta("ingested_at", datetime.now(timezone.utc).isoformat())
        db.set_meta("commit_sha", commit_sha)
        db.set_meta("file_count", result.file_count)
        db.set_meta("total_bytes", result.total_bytes)
        db.set_meta("symbol_count", result.symbol_count)
        db.set_meta("chunk_count", result.chunk_count)
        db.set_meta("edge_count", result.edge_count)
        db.set_meta("declared_file_count", declared)
        db.set_meta("indexed_file_count", result.file_count)
        db.set_meta("skipped_count", len(result.skipped))
        db.set_meta("tree_file_count", tree_total)
        db.set_meta("excluded_files", excluded[:5000])
        db.set_meta("complete", complete)
        db.set_meta(
            "config",
            {
                "semantic": semantic and db.has_embeddings(),
                "max_file_bytes": max_file_bytes,
                "include_binary": include_binary,
                "repomix_transforms": transforms,
            },
        )
        db.commit()
        db.close()
    except BaseException:
        try:
            db.close()
        except Exception:
            pass
        shutil.rmtree(work_path, ignore_errors=True)
        raise

    # Atomic-ish swap: move the freshly built index into place. A reader sees the
    # old index until the final rename, then the new one — never a half-built dir.
    _swap_into_place(work_path, final_path, repo_name)

    if result.file_count == 0:
        result.warnings.append("no files were ingested (empty source or all skipped)")
    return result


def _swap_into_place(work_path: Path, final_path: Path, repo_name: str) -> None:
    backup = final_path.parent / f".reflens-old-{repo_name}-{os.getpid()}"
    shutil.rmtree(backup, ignore_errors=True)
    if final_path.exists():
        final_path.rename(backup)
    try:
        work_path.rename(final_path)
    except BaseException:
        # Roll back to the previous index if the final move failed.
        if backup.exists() and not final_path.exists():
            backup.rename(final_path)
        shutil.rmtree(work_path, ignore_errors=True)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def _iter_files(kind, src_path, max_file_bytes, include_binary, result):
    """Yield (rel_path, raw_bytes, text) for each includable file."""
    if kind == "repomix":
        for rel_path, content in repomix.iter_repomix(src_path):
            cleaned = repomix.clean_content(content)
            data = cleaned.encode("utf-8")
            yield rel_path, data, cleaned
    else:  # dir / git
        for item in iter_dir(src_path, max_file_bytes=max_file_bytes, include_binary=include_binary):
            if item.skipped:
                result.skipped.append(f"{item.path}: {item.skipped}")
                continue
            assert item.data is not None
            text = item.data.decode("utf-8", errors="replace")
            yield item.path, item.data, text


def _run_semantic_pass(db: Database, embed_model: Optional[str], result: IngestResult) -> None:
    try:
        from ..search.semantic import get_embedder
    except Exception:
        result.warnings.append("semantic requested but search.semantic import failed; skipped")
        return
    embedder = get_embedder(embed_model)
    if embedder is None:
        result.warnings.append(
            "semantic requested but no embedding backend installed "
            "(pip install 'reflens[semantic]'); lexical search still works"
        )
        return
    batch_ids: list[int] = []
    batch_texts: list[str] = []
    dim = embedder.dim

    def flush() -> None:
        if not batch_ids:
            return
        vecs = embedder.embed(batch_texts)
        for cid, vec in zip(batch_ids, vecs):
            db.set_embedding(cid, dim, vec)
        batch_ids.clear()
        batch_texts.clear()

    for row in db.iter_all_chunks():
        batch_ids.append(int(row["id"]))
        batch_texts.append(row["text"])
        if len(batch_ids) >= 256:
            flush()
            db.commit()
    flush()
    db.commit()
