"""Ingest orchestrator: source -> blobs (Tier 2) + SQLite index (Tier 1 inputs).

Streams file-by-file and commits in batches so memory stays flat on huge inputs.
Rebuild is crash- and concurrency-safe: the new index is built in a temporary
directory and atomically swapped into place at the end, so a live MCP server
querying the repo never sees a half-built or missing index (re-ingesting while
serving is safe).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
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
    p = Path(source.rstrip("/"))
    stem = p.name
    for suffix in (".md", ".txt", ".git"):
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


def _pid_alive(pid: int) -> bool:
    """True if a process with this pid currently exists (signal 0 probes it)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    except OSError:
        return False
    return True


def _reap_stale_workdirs(repos_base: Path, repo_name: str) -> None:
    """Remove leftover ``.reflens-tmp-<repo>-<pid>`` build dirs from interrupted
    ingests, skipping our own and any whose pid is still alive — so a concurrent
    ingest of the same repo can't have its in-flight build dir deleted."""
    prefix = f".reflens-tmp-{repo_name}-"
    for stale in repos_base.glob(f"{prefix}*"):
        suffix = stale.name[len(prefix):]
        try:
            pid = int(suffix)
        except ValueError:
            continue  # unrecognized suffix — leave it untouched
        if pid == os.getpid() or _pid_alive(pid):
            continue
        shutil.rmtree(stale, ignore_errors=True)


def _looks_like_git_url(source: str) -> bool:
    s = source.strip()
    return s.startswith(("http://", "https://", "git://", "ssh://", "git@"))


def _clone_to_temp(url: str) -> tuple[Path, Path]:
    """Shallow-clone a remote git URL to a temp dir. Returns (repo_dir, temp_root).

    Depth 50 keeps it fast while giving enough history for the digest's recent-
    changes mining; the working tree at HEAD is complete, so Tier-2 losslessness
    is unaffected.
    """
    tmp = Path(tempfile.mkdtemp(prefix="reflens-clone-"))
    dest = tmp / "repo"
    try:
        subprocess.run(
            ["git", "clone", "--depth", "50", "--single-branch", url, str(dest)],
            capture_output=True, timeout=600, check=True,
        )
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        detail = exc.stderr.decode("utf-8", "replace")[:300] if exc.stderr else str(exc)
        raise RuntimeError(f"git clone failed for {url}: {detail}") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"git clone failed for {url}: {exc}") from exc
    return dest, tmp


def ingest_source(
    name: Optional[str],
    source: str,
    *,
    semantic: bool = False,
    embed_model: Optional[str] = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    include_binary: bool = False,
    reuse_embeddings: bool = True,
    progress: Optional[ProgressFn] = None,
) -> IngestResult:
    """Ingest a local directory, a Repomix .md dump, or a remote git URL.

    Remote URLs are shallow-cloned to a temp dir, ingested, then cleaned up; the
    stored ``source_ref`` is the URL.

    When ``reuse_embeddings`` is true (default), a re-ingest reuses any prior
    embedding whose symbol surface is unchanged instead of recomputing it — the
    expensive semantic pass then only runs over genuinely new/changed symbols.
    """
    clone_tmp: Optional[Path] = None
    if not Path(source).expanduser().exists() and _looks_like_git_url(source):
        clone_dest, clone_tmp = _clone_to_temp(source)
        classify_target = str(clone_dest)
    else:
        classify_target = source
    try:
        return _run_ingest(
            name=name,
            original_source=source,
            classify_target=classify_target,
            source_ref_display=source,
            semantic=semantic,
            embed_model=embed_model,
            max_file_bytes=max_file_bytes,
            include_binary=include_binary,
            reuse_embeddings=reuse_embeddings,
            progress=progress,
        )
    finally:
        if clone_tmp is not None:
            shutil.rmtree(clone_tmp, ignore_errors=True)


def _run_ingest(
    *,
    name: Optional[str],
    original_source: str,
    classify_target: str,
    source_ref_display: str,
    semantic: bool,
    embed_model: Optional[str],
    max_file_bytes: int,
    include_binary: bool,
    reuse_embeddings: bool,
    progress: Optional[ProgressFn],
) -> IngestResult:
    kind, src_path = _classify_source(classify_target)
    repo_name = paths.slugify_name(name) if name else derive_name(original_source)

    final_path = paths.repo_dir(repo_name)
    # The previous index (if any) is still live at final_path during this
    # out-of-place build, so we can read its embeddings to reuse unchanged ones.
    prev_db_path = final_path / "index.db"
    prev_db_path = prev_db_path if reuse_embeddings and prev_db_path.exists() else None
    repos_base = paths.repos_dir()
    repos_base.mkdir(parents=True, exist_ok=True)
    # Clean stale temp dirs left by a PREVIOUS interrupted ingest — but never one
    # whose pid is still alive (that would corrupt a concurrent ingest's build).
    _reap_stale_workdirs(repos_base, repo_name)
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
            _run_semantic_pass(db, embed_model, result, prev_db_path=prev_db_path)

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
        db.set_meta("source_ref", source_ref_display)
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
                # Record the embedding model so queries use the SAME model the
                # index was built with (custom models have different dims).
                "embed_model": embed_model,
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


def _load_reuse_map(
    prev_db_path: Optional[Path], fingerprint: str, dim: int
) -> dict[str, bytes]:
    """Map ``composed-symbol-text -> stored vector bytes`` from a prior index.

    A symbol's embedding is a deterministic function of its composed surface
    text under a fixed embedding pipeline, so an unchanged surface can reuse its
    exact prior vector bytes. Reuse is refused unless the prior index's stored
    ``embed_fingerprint`` exactly matches the current one (resolved model + dim +
    pipeline version) — vectors from a different model OR a different
    `compose_symbol_text` are not interchangeable, and a missing fingerprint
    (pre-0.3 index) is treated as incompatible. Each candidate vector is also
    length-checked against ``dim`` before reuse.

    The prior index is opened ``immutable=1`` (genuinely read-only — no `-shm`/
    `-wal` sidecars are written into the live prior directory), which is safe
    because nothing writes the old index during this out-of-place build. Reuse
    is a pure optimization: ANY error/incompatibility returns ``{}`` and the
    caller falls back to a full embed — it must never crash or alter an ingest.

    Memory note: this materializes one dict of all prior vectors (bounded by the
    prior symbol count), separate from the streaming Tier-2 file pass.
    """
    try:
        if prev_db_path is None or not Path(prev_db_path).exists():
            return {}
    except OSError:
        return {}
    try:
        from urllib.request import pathname2url

        from ..search.semantic import compose_symbol_text

        uri = f"file:{pathname2url(str(Path(prev_db_path)))}?immutable=1"
        conn = sqlite3.connect(uri, uri=True)
    except Exception:
        return {}
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT value FROM meta WHERE key='embed_fingerprint'"
        ).fetchone()
        prior_fp = None
        if row is not None:
            try:
                parsed = json.loads(row["value"])
                prior_fp = parsed if isinstance(parsed, str) else None
            except (ValueError, TypeError):
                prior_fp = None
        if prior_fp is None or prior_fp != fingerprint:
            return {}  # unknown/different embedding pipeline => not portable
        vec_nbytes = dim * 4  # float32
        out: dict[str, bytes] = {}
        for r in conn.execute(
            "SELECT s.kind, s.name, s.signature, s.docstring, e.vec, e.dim "
            "FROM symbols s JOIN embeddings e "
            "ON e.unit='symbol' AND e.unit_id = s.id"
        ):
            vec = r["vec"]
            if (
                r["dim"] != dim
                or not isinstance(vec, (bytes, bytearray))
                or len(vec) != vec_nbytes
            ):
                continue
            text = compose_symbol_text(r["kind"], r["name"], r["signature"], r["docstring"])
            out[text] = bytes(vec)
        return out
    except Exception:
        # Optimization only: any failure (incompatible schema, read error, …)
        # degrades to a full embed rather than breaking the ingest.
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _run_semantic_pass(
    db: Database,
    embed_model: Optional[str],
    result: IngestResult,
    *,
    prev_db_path: Optional[Path] = None,
) -> None:
    try:
        from ..search.semantic import compose_symbol_text, embed_fingerprint, get_embedder
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
    dim = embedder.dim
    fingerprint = embed_fingerprint(embedder)
    # Reuse unchanged vectors from the prior index so the embed pass only runs
    # over genuinely new/changed symbol surfaces (re-ingest goes from minutes to
    # seconds when little changed). Empty unless the prior index's embedding
    # pipeline fingerprint matches exactly.
    reuse = _load_reuse_map(prev_db_path, fingerprint, dim)

    batch_ids: list[int] = []
    batch_texts: list[str] = []

    def flush() -> None:
        if not batch_ids:
            return
        vecs = embedder.embed(batch_texts)
        for sid, vec in zip(batch_ids, vecs):
            db.set_embedding("symbol", sid, dim, vec)
        result.embedded_symbols += len(batch_ids)
        batch_ids.clear()
        batch_texts.clear()

    # Embed the dense SYMBOL surface (signature + docstring), not raw code bodies:
    # ~6x faster and a better concept-match target. Full content stays in Tier 2
    # and lexical FTS, so byte-exact retrieval is unaffected.
    for row in db.conn.execute("SELECT id, kind, name, signature, docstring FROM symbols"):
        text = compose_symbol_text(row["kind"], row["name"], row["signature"], row["docstring"])
        cached = reuse.get(text)
        if cached is not None:
            db.set_embedding("symbol", int(row["id"]), dim, cached)
            result.reused_embeddings += 1
            continue
        batch_ids.append(int(row["id"]))
        batch_texts.append(text)
        if len(batch_ids) >= 256:
            flush()
            db.commit()
    flush()
    # Record the pipeline fingerprint so a later ingest can prove its stored
    # vectors are reuse-compatible with this one (or refuse if not).
    db.set_meta("embed_fingerprint", fingerprint)
    db.commit()
    if not db.has_embeddings():
        result.warnings.append(
            "semantic: repo has no extractable symbols to embed; lexical search still applies"
        )
