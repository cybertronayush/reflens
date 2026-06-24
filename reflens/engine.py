"""The Repo facade — the single surface CLI and MCP both call.

Opens a repo's SQLite index + blob store and exposes map / search / read /
neighbors / verify. Keeping this the one seam means the CLI and the MCP server
can never drift in behavior.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

from . import paths
from .digest import build_digest, render_digest
from .graph import neighbors as _neighbors
from .models import Hit
from .search import search as _search
from .store import BlobStore, Database

DEFAULT_READ_MAX_LINES = 1500


def list_repos() -> list[dict[str, Any]]:
    base = paths.repos_dir()
    if not base.exists():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        dbp = child / "index.db"
        if not dbp.exists():
            continue
        try:
            db = Database.open(dbp)
            m = db.all_meta()
            db.close()
        except Exception:
            m = {}
        out.append(
            {
                "name": child.name,
                "files": m.get("file_count"),
                "symbols": m.get("symbol_count"),
                "chunks": m.get("chunk_count"),
                "source": m.get("source_ref"),
                "kind": m.get("source_kind"),
                "ingested_at": m.get("ingested_at"),
                "semantic": bool((m.get("config") or {}).get("semantic")),
            }
        )
    return out


def remove_repo(name: str) -> bool:
    slug = paths.slugify_name(name)
    d = paths.repo_dir(slug)
    if not d.exists():
        return False
    shutil.rmtree(d)
    return True


def _slice_lines(content: str, start: Optional[int], end: Optional[int]) -> tuple[str, int, int, int, bool]:
    lines = content.split("\n")
    total = len(lines)
    truncated = False
    if start is None and end is None:
        if total > DEFAULT_READ_MAX_LINES:
            s, e = 1, DEFAULT_READ_MAX_LINES
            truncated = True
        else:
            s, e = 1, total
    else:
        s = max(1, start or 1)
        e = min(total, end or total)
        if e < s:
            e = s
    body = "\n".join(lines[s - 1: e])
    return body, s, e, total, truncated


class Repo:
    def __init__(self, name: str, db: Database, blobs: BlobStore) -> None:
        self.name = name
        self.db = db
        self.blobs = blobs

    @classmethod
    def open(cls, name: str) -> "Repo":
        slug = paths.slugify_name(name)
        dbp = paths.db_path(slug)
        if not dbp.exists():
            raise FileNotFoundError(
                f"reference repo {name!r} not found. Run `reflens add <source> --name {slug}` first, "
                f"or `reflens list` to see what's available."
            )
        db = Database.open(dbp)
        blobs = BlobStore(paths.blobs_dir(slug))
        return cls(slug, db, blobs)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Repo":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- map (Tier-1 digest) --------------------------------------------
    def map(
        self, *, level: int = 1, budget_tokens: int = 120_000, path_glob: Optional[str] = None
    ) -> tuple[str, dict[str, Any]]:
        digest = build_digest(self.db, self.blobs, path_glob=path_glob, include_symbols=level >= 1)
        return render_digest(digest, level=level, budget_tokens=budget_tokens)

    # ---- modules (lightweight nav menu / table of contents) -------------
    def modules(self) -> list[dict[str, Any]]:
        from collections import Counter, defaultdict

        from .graph import resolve

        dep = resolve.internal_dependents(self.db)
        rows = self.db.list_files()
        grouped: dict[str, list] = defaultdict(list)
        for r in rows:
            top = r["path"].split("/")[0] if "/" in r["path"] else "(root)"
            grouped[top].append(r)
        out: list[dict[str, Any]] = []
        for top, rs in grouped.items():
            langs = Counter(x["lang"] for x in rs)
            out.append({
                "name": top,
                "files": len(rs),
                "langs": [lng for lng, _ in langs.most_common(3)],
                "dependents": sum(dep.get(x["path"], 0) for x in rs),
            })
        out.sort(key=lambda m: (-m["files"], m["name"]))
        return out

    # ---- search ----------------------------------------------------------
    def search(self, query: str, *, k: int = 10, mode: str = "auto") -> list[Hit]:
        return _search(self.db, query, k=k, mode=mode)

    # ---- read (Tier-2, byte-exact) --------------------------------------
    def read(
        self, target: str, *, start: Optional[int] = None, end: Optional[int] = None
    ) -> dict[str, Any]:
        frow = self.db.get_file_by_path(target)
        if frow is not None:
            content = self.blobs.get_text(frow["sha256"])
            body, s, e, total, truncated = _slice_lines(content, start, end)
            res = {
                "kind": "file",
                "path": frow["path"],
                "lang": frow["lang"],
                "sha256": frow["sha256"],
                "total_lines": total,
                "start_line": s,
                "end_line": e,
                "content": body,
            }
            if truncated:
                res["truncated"] = True
                res["note"] = (
                    f"file has {total} lines; returned 1-{e}. Request an explicit "
                    "start/end to page through the rest (byte-exact)."
                )
            return res

        # symbol lookup
        syms = self.db.find_symbols_by_name(target, limit=10)
        if not syms:
            return {"kind": "not_found", "target": target,
                    "error": "no file path or symbol with that name. Use reflens_search to locate it."}
        candidates = [
            {"path": s["path"], "symbol": s["name"], "kind": s["kind"],
             "decl": s["signature"], "start_line": s["start_line"], "end_line": s["end_line"]}
            for s in syms
        ]
        bodies = []
        if len(syms) <= 3:
            for s in syms:
                frow = self.db.get_file_by_path(s["path"])
                if frow is None:
                    continue
                content = self.blobs.get_text(frow["sha256"])
                body, bs, be, _, _ = _slice_lines(content, s["start_line"], s["end_line"])
                bodies.append({"path": s["path"], "symbol": s["name"],
                               "start_line": bs, "end_line": be, "content": body})
        return {"kind": "symbol", "target": target, "matches": candidates, "bodies": bodies}

    # ---- neighbors -------------------------------------------------------
    def neighbors(self, target: str, *, limit: int = 50) -> dict[str, Any]:
        return _neighbors(self.db, target, limit=limit)

    # ---- history (on-demand historical context from live git) -----------
    def history(self, target: Optional[str] = None, *, limit: int = 25) -> dict[str, Any]:
        m = self.db.all_meta()
        src = m.get("source_ref")
        kind = m.get("source_kind")
        if kind != "dir" or not src or not Path(src).exists():
            return {
                "available": False,
                "reason": f"git history needs a live git source directory; this repo was "
                f"ingested from {kind!r}. Re-ingest from the repo directory to enable history.",
            }
        from .ingest import gitmeta

        src_path = Path(src)
        if target:
            commits = gitmeta.file_history(src_path, target, limit)
        else:
            commits = gitmeta.commits_with_dates(src_path, limit)
        return {"available": True, "target": target or "(repo)", "commits": commits}

    # ---- verify (proves losslessness AND completeness) ------------------
    def verify(self) -> dict[str, Any]:
        from .store.blobs import sha256_hex

        # 1) Storage integrity: every stored blob decompresses to its hash.
        total = 0
        verified = 0
        failed: list[str] = []
        for row in self.db.list_files():
            total += 1
            try:
                raw = self.blobs.get(row["sha256"])  # get() already re-checks the hash
                if sha256_hex(raw) == row["sha256"]:
                    verified += 1
                else:
                    failed.append(row["path"])
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{row['path']}: {exc}")

        # 2) Completeness: every file the source declared was captured.
        m = self.db.all_meta()
        declared = m.get("declared_file_count")
        indexed = m.get("indexed_file_count", total)
        skipped = m.get("skipped_count", 0)
        complete_at_ingest = bool(m.get("complete", True))
        excluded_known = len(m.get("excluded_files", []) or [])
        src = m.get("source_ref")
        kind = m.get("source_kind")

        recheck_declared: Optional[int] = None
        drift = False
        source_available = bool(src) and Path(src).exists()
        if source_available and kind == "repomix":
            from .ingest import repomix

            recheck_declared = repomix.count_entries(Path(src))
            drift = recheck_declared != indexed

        completeness = {
            "declared_files": declared,
            "indexed_files": indexed,
            "skipped_files": skipped,
            "excluded_known": excluded_known,
            "complete_at_ingest": complete_at_ingest,
            "source_available": source_available,
            "recheck_declared": recheck_declared,
            "drift_detected": drift,
        }

        # 3) Extraction coverage: % of code files that yielded ≥1 symbol.
        from .extract.registry import _CODE_LANGS

        langs = tuple(_CODE_LANGS)
        ph = ",".join("?" * len(langs))
        code_files = int(
            self.db.conn.execute(
                f"SELECT COUNT(*) c FROM files WHERE lang IN ({ph})", langs
            ).fetchone()["c"]
        )
        with_syms = int(
            self.db.conn.execute(
                f"SELECT COUNT(DISTINCT s.file_id) c FROM symbols s "
                f"JOIN files f ON f.id=s.file_id WHERE f.lang IN ({ph})",
                langs,
            ).fetchone()["c"]
        )
        extraction = {
            "code_files": code_files,
            "with_symbols": with_syms,
            "coverage_pct": round(100 * with_syms / code_files, 1) if code_files else 100.0,
        }

        ok = bool(not failed and total > 0 and complete_at_ingest and not drift)
        return {
            "repo": self.name,
            "files": total,
            "verified": verified,
            "failed": failed,
            "completeness": completeness,
            "extraction": extraction,
            "ok": ok,
        }

    def meta(self) -> dict[str, Any]:
        return self.db.all_meta()
