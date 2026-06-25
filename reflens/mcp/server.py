"""Stdlib MCP stdio server: JSON-RPC 2.0, newline-delimited, over stdin/stdout.

Why hand-rolled instead of the `mcp` SDK: zero dependency (works on any Python,
including 3.14 where the SDK may lack a wheel), and full control of the wire
format. Only protocol methods we need are implemented: initialize,
notifications/initialized, tools/list, tools/call, ping.

Contract notes:
  - stdout carries ONLY JSON-RPC messages (one compact JSON object per line).
    All diagnostics go to stderr. Violating this corrupts the channel.
  - Tool handlers never raise out of the loop; failures become isError results.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

from .. import __version__
from ..engine import Repo, list_repos

_PROTOCOL_DEFAULT = "2025-06-18"
_SERVER_INFO = {"name": "reflens", "version": __version__}


# --------------------------------------------------------------------------
# Tool catalog (the DX contract the agent reads to decide what to call)
# --------------------------------------------------------------------------
def tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "reflens_list",
            "description": (
                "List the reference repositories indexed by reflens (name, file/symbol "
                "counts, source, whether semantic search is on). Call this first to see "
                "what reference code is available."
            ),
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "reflens_map",
            "description": (
                "Get the Tier-1 Intelligence Digest of a reference repo. DEFAULT (level 0) is "
                "a small architecture brief: modules + responsibilities, internal centrality "
                "(most-depended-on files), entry points, mined decisions/conventions, language "
                "mix. ALWAYS START HERE — it's a few thousand tokens. To see code signatures, "
                "scope to a module: reflens_map(repo, path_glob='<module>/**', level=2). A full "
                "repo at level 1/2 can be 100K+ tokens, so always pair levels 1/2 with a "
                "path_glob. Lossy on bodies; use reflens_read for exact source."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Reference repo name (see reflens_list)."},
                    "level": {"type": "integer", "enum": [0, 1, 2], "default": 0,
                              "description": "0=architecture brief (default), 1=+outlines(top-level), 2=+methods. Pair 1/2 with path_glob."},
                    "path_glob": {"type": "string",
                                  "description": "fnmatch filter to scope to a subtree, e.g. 'crates/**'. Required in practice for level 1/2 on big repos."},
                    "budget_tokens": {"type": "integer", "default": 25000,
                                      "description": "Max tokens for the digest; truncates with a drill-down pointer if exceeded."},
                },
                "required": ["repo"],
                "additionalProperties": False,
            },
        },
        {
            "name": "reflens_modules",
            "description": (
                "Compact table-of-contents for a reference repo: its top-level modules with "
                "file counts, languages, and internal-dependency weight. Cheap nav menu — call "
                "this (or reflens_map) first, then drill into a module with "
                "reflens_map(path_glob='<module>/**', level=2)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"repo": {"type": "string"}},
                "required": ["repo"],
                "additionalProperties": False,
            },
        },
        {
            "name": "reflens_search",
            "description": (
                "Search a reference repo for relevant code/text. Hybrid lexical (FTS5) + "
                "semantic (when enabled), fused by reciprocal-rank. Returns ranked hits with "
                "path + line range + snippet. Use this to find where something is implemented, "
                "then reflens_read the exact source."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "query": {"type": "string"},
                    "k": {"type": "integer", "default": 10, "description": "Max results."},
                    "mode": {"type": "string", "enum": ["auto", "lexical", "semantic", "hybrid"],
                             "default": "auto"},
                },
                "required": ["repo", "query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "reflens_read",
            "description": (
                "Retrieve BYTE-EXACT source from a reference repo (Tier 2, lossless). "
                "target is either a file path (optionally with start/end line range) or a "
                "symbol name (returns the symbol's definition body). This is the safety layer: "
                "when the digest isn't enough, pull the real code with zero paraphrase."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "target": {"type": "string", "description": "File path or symbol name."},
                    "start": {"type": "integer", "description": "1-indexed start line (file targets)."},
                    "end": {"type": "integer", "description": "1-indexed end line (inclusive)."},
                },
                "required": ["repo", "target"],
                "additionalProperties": False,
            },
        },
        {
            "name": "reflens_neighbors",
            "description": (
                "Expand the dependency graph around a file or symbol: what it imports, what "
                "imports it, and what it defines. Use to follow relationships across the repo."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "target": {"type": "string", "description": "File path or symbol name."},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": ["repo", "target"],
                "additionalProperties": False,
            },
        },
        {
            "name": "reflens_history",
            "description": (
                "Historical context from the reference repo's live git history: recent commits "
                "for the whole repo, or the change history of a specific file (hash, date, "
                "author, subject). Use to understand how/why code evolved. Only available when "
                "the repo was ingested from a live git directory."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "target": {"type": "string", "description": "Optional file path; omit for repo-wide."},
                    "limit": {"type": "integer", "default": 25},
                },
                "required": ["repo"],
                "additionalProperties": False,
            },
        },
        {
            "name": "reflens_verify",
            "description": (
                "Prove losslessness: reconstruct every stored file and compare SHA-256 against "
                "ingest. Returns counts and any failures. Use to confirm the reference is intact."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"repo": {"type": "string"}},
                "required": ["repo"],
                "additionalProperties": False,
            },
        },
    ]


# --------------------------------------------------------------------------
# Tool result formatting (text content)
# --------------------------------------------------------------------------
def _fmt_list() -> str:
    repos = list_repos()
    if not repos:
        return "No reference repos indexed yet. Run `reflens add <dir|git-url|repomix.md>`."
    lines = ["Reference repos:"]
    for r in repos:
        sem = " · semantic" if r["semantic"] else ""
        lines.append(
            f"- {r['name']}: {r['files']} files, {r['symbols']} symbols, "
            f"{r['chunks']} chunks{sem}  (source: {r['kind']})"
        )
    return "\n".join(lines)


def _fmt_modules(repo: str, mods: list) -> str:
    if not mods:
        return f"{repo}: no modules."
    out = [f"{repo} — {len(mods)} modules (drill in with reflens_map path_glob='<module>/**'):"]
    for m in mods:
        langs = ",".join(m["langs"])
        out.append(f"- {m['name']}/  {m['files']} files [{langs}]  · {m['dependents']} internal deps")
    return "\n".join(out)


def _fmt_search(repo: str, query: str, hits: list, mode: str) -> str:
    if not hits:
        return f'No results for "{query}" in {repo}.'
    out = [f'{len(hits)} results for "{query}" in {repo} (mode={mode}):', ""]
    for i, h in enumerate(hits, 1):
        head = h.snippet.split("\n", 1)[0][:140]
        out.append(f"{i}. [{h.kind}/{h.source}] {h.path}:{h.start_line}-{h.end_line}  (score {h.score})")
        out.append(f"   {head}")
    out.append("")
    out.append('→ reflens_read(repo, "<path>", start, end) for byte-exact source.')
    return "\n".join(out)


def _fmt_read(res: dict) -> str:
    kind = res.get("kind")
    if kind == "file":
        head = (
            f"{res['path']}  ({res['lang']})  lines {res['start_line']}-{res['end_line']} "
            f"of {res['total_lines']}  sha={res['sha256'][:12]}"
        )
        body = res["content"]
        note = f"\n\n[{res['note']}]" if res.get("truncated") else ""
        return f"{head}\n```\n{body}\n```{note}"
    if kind == "symbol":
        parts = [f'Symbol "{res["target"]}" — {len(res["matches"])} definition(s):']
        for m in res["matches"]:
            parts.append(f"- {m['path']}:{m['start_line']}-{m['end_line']}  {m['kind']} `{m['decl']}`")
        for b in res.get("bodies", []):
            parts.append(f"\n{b['path']}:{b['start_line']}-{b['end_line']}\n```\n{b['content']}\n```")
        if not res.get("bodies"):
            parts.append("\nMultiple matches; call reflens_read with a specific path + line range.")
        return "\n".join(parts)
    return res.get("error", "not found")


def _fmt_neighbors(res: dict) -> str:
    return json.dumps(res, indent=2, ensure_ascii=False)


def _fmt_history(res: dict) -> str:
    if not res.get("available"):
        return res.get("reason", "history unavailable")
    out = [f"History for {res['target']} ({len(res['commits'])} commits):"]
    for c in res["commits"]:
        out.append(f"- {c['date']} {c['hash']} ({c['author']}): {c['subject']}")
    return "\n".join(out)


def _fmt_verify(res: dict) -> str:
    status = "OK — lossless" if res["ok"] else "FAILED"
    out = [f"verify {res['repo']}: {status}",
           f"files={res['files']} verified={res['verified']} failed={len(res['failed'])}"]
    if res["failed"]:
        out.append("failures:")
        out.extend(f"  - {f}" for f in res["failed"][:50])
    return "\n".join(out)


def handle_call(name: str, args: dict[str, Any]) -> tuple[str, bool]:
    """Return (text, is_error). Never raises."""
    try:
        if name == "reflens_list":
            return _fmt_list(), False

        repo_name = args.get("repo")
        if not repo_name:
            return "missing required argument: repo", True

        if name == "reflens_map":
            with Repo.open(repo_name) as r:
                text, stats = r.map(
                    level=int(args.get("level", 0)),
                    budget_tokens=int(args.get("budget_tokens", 25000)),
                    path_glob=args.get("path_glob"),
                )
            footer = (
                f"\n\n---\n[digest: ~{stats['tokens_est']} tokens, "
                f"{stats['files_shown']}/{stats['files_total']} files shown, "
                f"level {stats['level']}{', TRUNCATED' if stats['truncated'] else ''}]"
            )
            return text + footer, False

        if name == "reflens_modules":
            with Repo.open(repo_name) as r:
                mods = r.modules()
            return _fmt_modules(repo_name, mods), False

        if name == "reflens_search":
            query = args.get("query", "")
            mode = args.get("mode", "auto")
            with Repo.open(repo_name) as r:
                hits = r.search(query, k=int(args.get("k", 10)), mode=mode)
            return _fmt_search(repo_name, query, hits, mode), False

        if name == "reflens_read":
            target = args.get("target")
            if not target:
                return "missing required argument: target (a file path or symbol name)", True
            with Repo.open(repo_name) as r:
                res = r.read(target, start=args.get("start"), end=args.get("end"))
            return _fmt_read(res), False

        if name == "reflens_neighbors":
            target = args.get("target")
            if not target:
                return "missing required argument: target (a file path or symbol name)", True
            with Repo.open(repo_name) as r:
                res = r.neighbors(target, limit=int(args.get("limit", 50)))
            return _fmt_neighbors(res), False

        if name == "reflens_history":
            with Repo.open(repo_name) as r:
                res = r.history(args.get("target"), limit=int(args.get("limit", 25)))
            return _fmt_history(res), False

        if name == "reflens_verify":
            with Repo.open(repo_name) as r:
                res = r.verify()
            return _fmt_verify(res), False

        return f"unknown tool: {name}", True
    except FileNotFoundError as exc:
        return str(exc), True
    except Exception as exc:  # noqa: BLE001
        return f"reflens error in {name}: {type(exc).__name__}: {exc}", True


# --------------------------------------------------------------------------
# JSON-RPC plumbing
# --------------------------------------------------------------------------
def _result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_message(msg: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Dispatch one JSON-RPC message. Returns a response, or None for notifications."""
    method = msg.get("method")
    req_id = msg.get("id")
    is_notification = "id" not in msg

    if method == "initialize":
        params = msg.get("params") or {}
        proto = params.get("protocolVersion") or _PROTOCOL_DEFAULT
        return _result(
            req_id,
            {
                "protocolVersion": proto,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": _SERVER_INFO,
            },
        )

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return _result(req_id, {})

    if method == "tools/list":
        return _result(req_id, {"tools": tool_specs()})

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        text, is_error = handle_call(name, args if isinstance(args, dict) else {})
        return _result(
            req_id, {"content": [{"type": "text", "text": text}], "isError": is_error}
        )

    if is_notification:
        return None
    return _error(req_id, -32601, f"method not found: {method}")


def serve() -> int:
    """Run the stdio loop until EOF. Returns process exit code."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    print("reflens MCP server ready (stdio)", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        if isinstance(msg, list):  # batch
            for sub in msg:
                resp = handle_message(sub)
                if resp is not None:
                    _write(resp)
            continue
        resp = handle_message(msg)
        if resp is not None:
            _write(resp)
    return 0


def _write(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()
