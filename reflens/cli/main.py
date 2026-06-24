"""reflens CLI.

Commands:
  add <source>      ingest a dir / git repo / repomix .md into a reference repo
  list              list indexed reference repos
  map <name>        print the Tier-1 digest (the whole-repo overview)
  search <name> q   hybrid search
  read <name> tgt   byte-exact source (file path or symbol)
  neighbors <name> tgt   dependency expansion
  verify <name>     prove losslessness (SHA-256 round-trip)
  remove <name>     delete an indexed repo
  serve             run the MCP stdio server (used by OpenCode / Claude Code)
  install [hosts]   register the MCP server (opencode, claude, or both)
  uninstall [hosts] unregister
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .. import __version__
from ..engine import Repo, list_repos, remove_repo
from ..ingest import ingest_source
from ..ingest.walker import DEFAULT_MAX_FILE_BYTES


def _eprint(*a: object) -> None:
    print(*a, file=sys.stderr)


def _cmd_add(args: argparse.Namespace) -> int:
    def progress(n: int, path: str) -> None:
        _eprint(f"  …{n} files (last: {path})")

    _eprint(f"Ingesting {args.source} …")
    try:
        res = ingest_source(
            args.name,
            args.source,
            semantic=args.semantic,
            embed_model=args.embed_model,
            max_file_bytes=args.max_file_bytes,
            include_binary=args.include_binary,
            progress=progress,
        )
    except (FileNotFoundError, ValueError) as exc:
        _eprint(f"error: {exc}")
        return 2
    print(
        f"Indexed '{res.name}': {res.file_count} files, {res.symbol_count} symbols, "
        f"{res.chunk_count} chunks, {res.edge_count} edges."
    )
    if res.skipped:
        print(f"  skipped {len(res.skipped)} files (binary/oversized). First few:")
        for s in res.skipped[:5]:
            print(f"    - {s}")
    for w in res.warnings:
        _eprint(f"  ! {w}")
    print(f"Verify losslessness:  reflens verify {res.name}")
    print(f"Overview:             reflens map {res.name}")
    return 0


def _cmd_list(_args: argparse.Namespace) -> int:
    repos = list_repos()
    if not repos:
        print("No reference repos. Add one:  reflens add <dir|git-url|repomix.md>")
        return 0
    for r in repos:
        sem = " [semantic]" if r["semantic"] else ""
        print(f"{r['name']}{sem}")
        print(f"    {r['files']} files · {r['symbols']} symbols · {r['chunks']} chunks · {r['kind']}")
        print(f"    source: {r['source']}")
    return 0


def _cmd_map(args: argparse.Namespace) -> int:
    with Repo.open(args.name) as r:
        text, stats = r.map(level=args.level, budget_tokens=args.budget, path_glob=args.glob)
    if args.output:
        with open(args.output, "w") as f:
            f.write(text)
        _eprint(f"wrote {stats['tokens_est']} tokens (~) to {args.output}")
    else:
        print(text)
    _eprint(
        f"[digest ~{stats['tokens_est']} tokens, {stats['files_shown']}/{stats['files_total']} files"
        f"{', TRUNCATED' if stats['truncated'] else ''}]"
    )
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    with Repo.open(args.name) as r:
        hits = r.search(args.query, k=args.k, mode=args.mode)
    if args.json:
        print(json.dumps([h.__dict__ for h in hits], indent=2))
        return 0
    if not hits:
        print(f'No results for "{args.query}".')
        return 0
    for i, h in enumerate(hits, 1):
        print(f"{i}. [{h.kind}/{h.source}] {h.path}:{h.start_line}-{h.end_line}  ({h.score})")
        print(f"   {h.snippet.splitlines()[0][:150] if h.snippet else ''}")
    return 0


def _cmd_read(args: argparse.Namespace) -> int:
    with Repo.open(args.name) as r:
        res = r.read(args.target, start=args.start, end=args.end)
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    kind = res.get("kind")
    if kind == "file":
        _eprint(f"# {res['path']} ({res['lang']}) lines {res['start_line']}-{res['end_line']}/{res['total_lines']}")
        print(res["content"])
        if res.get("truncated"):
            _eprint(f"[{res['note']}]")
    elif kind == "symbol":
        for m in res["matches"]:
            _eprint(f"# {m['path']}:{m['start_line']}-{m['end_line']} {m['kind']} {m['decl']}")
        for b in res.get("bodies", []):
            _eprint(f"\n# {b['path']}:{b['start_line']}-{b['end_line']}")
            print(b["content"])
    else:
        _eprint(res.get("error", "not found"))
        return 1
    return 0


def _cmd_neighbors(args: argparse.Namespace) -> int:
    with Repo.open(args.name) as r:
        res = r.neighbors(args.target, limit=args.limit)
    print(json.dumps(res, indent=2))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    with Repo.open(args.name) as r:
        res = r.verify()
    print(json.dumps(res, indent=2))
    return 0 if res["ok"] else 1


def _cmd_remove(args: argparse.Namespace) -> int:
    if not args.yes:
        ans = input(f"Delete reference repo '{args.name}'? [y/N] ").strip().lower()
        if ans != "y":
            print("aborted")
            return 1
    ok = remove_repo(args.name)
    print(f"removed {args.name}" if ok else f"{args.name} not found")
    return 0 if ok else 1


def _cmd_serve(_args: argparse.Namespace) -> int:
    from ..mcp import serve

    return serve()


def _cmd_install(args: argparse.Namespace) -> int:
    from .install import install

    for msg in install(args.hosts, name=args.name):
        print(msg)
    print("\nRestart OpenCode / Claude Code to load the server, then ask it to use `reflens_list`.")
    return 0


def _cmd_uninstall(args: argparse.Namespace) -> int:
    from .install import uninstall

    for msg in uninstall(args.hosts, name=args.name):
        print(msg)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reflens", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"reflens {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add", help="ingest a source into a reference repo")
    a.add_argument("source", help="directory, git repo path, or repomix .md file")
    a.add_argument("--name", help="repo name (default: derived from source)")
    a.add_argument("--semantic", action="store_true", help="also build vector embeddings (needs reflens[semantic])")
    a.add_argument("--embed-model", default=None, help="embedding model name (fastembed)")
    a.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES,
                   help=f"skip files larger than this (default {DEFAULT_MAX_FILE_BYTES})")
    a.add_argument("--include-binary", action="store_true", help="also store binary files")
    a.set_defaults(func=_cmd_add)

    sub.add_parser("list", help="list indexed reference repos").set_defaults(func=_cmd_list)

    m = sub.add_parser("map", help="print the Tier-1 intelligence digest")
    m.add_argument("name")
    m.add_argument("--level", type=int, choices=[0, 1, 2], default=1)
    m.add_argument("--glob", default=None, help="fnmatch path filter, e.g. 'src/**'")
    m.add_argument("--budget", type=int, default=120000, help="token budget")
    m.add_argument("-o", "--output", default=None, help="write to file instead of stdout")
    m.set_defaults(func=_cmd_map)

    s = sub.add_parser("search", help="hybrid search")
    s.add_argument("name")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=10)
    s.add_argument("--mode", choices=["auto", "lexical", "semantic", "hybrid"], default="auto")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_search)

    rd = sub.add_parser("read", help="byte-exact source (file path or symbol)")
    rd.add_argument("name")
    rd.add_argument("target")
    rd.add_argument("--start", type=int, default=None)
    rd.add_argument("--end", type=int, default=None)
    rd.add_argument("--json", action="store_true")
    rd.set_defaults(func=_cmd_read)

    nb = sub.add_parser("neighbors", help="dependency expansion")
    nb.add_argument("name")
    nb.add_argument("target")
    nb.add_argument("--limit", type=int, default=50)
    nb.set_defaults(func=_cmd_neighbors)

    v = sub.add_parser("verify", help="prove losslessness (SHA-256 round-trip)")
    v.add_argument("name")
    v.set_defaults(func=_cmd_verify)

    rm = sub.add_parser("remove", help="delete an indexed repo")
    rm.add_argument("name")
    rm.add_argument("-y", "--yes", action="store_true")
    rm.set_defaults(func=_cmd_remove)

    sub.add_parser("serve", help="run the MCP stdio server").set_defaults(func=_cmd_serve)

    ins = sub.add_parser("install", help="register the MCP server with a host")
    ins.add_argument("hosts", nargs="*", default=["both"], help="opencode claude (default: both)")
    ins.add_argument("--name", default="reflens", help="MCP server name in the host config")
    ins.set_defaults(func=_cmd_install)

    un = sub.add_parser("uninstall", help="unregister the MCP server")
    un.add_argument("hosts", nargs="*", default=["both"])
    un.add_argument("--name", default="reflens")
    un.set_defaults(func=_cmd_uninstall)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        _eprint(f"error: {exc}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
