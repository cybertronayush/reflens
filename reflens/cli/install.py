"""Wire the reflens MCP server into MCP hosts (OpenCode + Claude Code).

Each host has its own JSON shape (verified against live configs):
  OpenCode  ~/.config/opencode/opencode.json -> mcp.<name>:
      {"type":"local","command":[argv...],"environment":{...},"enabled":true}
  Claude    ~/.claude.json                    -> mcpServers.<name>:
      {"command":exe,"args":[...],"env":{...},"type":"stdio"}

The server is launched as ``<this-python> -m reflens serve`` so the right
interpreter+package is used regardless of PATH. Writes are atomic (tmp+replace)
and make a one-time ``.bak`` so an existing config is never lost.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

DEFAULT_SERVER_NAME = "reflens"

_SNIPPET_START = "<!-- reflens:start -->"
_SNIPPET_END = "<!-- reflens:end -->"
_SNIPPET_BODY = """## reflens — reference repository context (MCP)

When a task references another codebase ("learn from X repo", "apply the pattern
from Y", "how does Z do this"), use the `reflens_*` MCP tools instead of guessing.
reflens holds large reference repos losslessly and serves them on demand.

Workflow:
1. `reflens_list` — which reference repos are indexed.
2. `reflens_modules(repo)` then `reflens_map(repo)` — architecture overview (start here).
3. `reflens_map(repo, path_glob="<module>/**", level=2)` — drill into a module.
4. `reflens_search(repo, "<query>")` — find relevant code.
5. `reflens_read(repo, "<path|symbol>", start?, end?)` — byte-exact source.
6. `reflens_neighbors` / `reflens_history` — dependencies / git history.

Reason from the digest; drop to `reflens_read` for exact implementation. Never
claim knowledge of a reference repo you have not queried with these tools."""


def _agents_targets() -> list[Path]:
    return [
        Path.home() / ".config" / "opencode" / "AGENTS.md",
        Path.home() / ".claude" / "CLAUDE.md",
    ]


def write_agent_guidance() -> list[str]:
    """Insert/refresh the reflens usage block in global agent instruction files."""
    block = f"{_SNIPPET_START}\n{_SNIPPET_BODY}\n{_SNIPPET_END}"
    msgs: list[str] = []
    for p in _agents_targets():
        existing = p.read_text() if p.exists() else ""
        if _SNIPPET_START in existing and _SNIPPET_END in existing:
            new = re.sub(
                re.escape(_SNIPPET_START) + r".*?" + re.escape(_SNIPPET_END),
                block, existing, flags=re.S,
            )
            action = "refreshed"
        else:
            new = (existing.rstrip() + "\n\n" + block + "\n") if existing.strip() else block + "\n"
            action = "added"
        p.parent.mkdir(parents=True, exist_ok=True)
        bak = p.with_suffix(p.suffix + ".bak")
        if p.exists() and not bak.exists():
            shutil.copy2(p, bak)
        p.write_text(new)
        msgs.append(f"agent guidance: {action} reflens block in {p}")
    return msgs


def remove_agent_guidance() -> list[str]:
    block_re = re.compile(re.escape(_SNIPPET_START) + r".*?" + re.escape(_SNIPPET_END) + r"\n?", re.S)
    msgs: list[str] = []
    for p in _agents_targets():
        if not p.exists():
            continue
        existing = p.read_text()
        if _SNIPPET_START in existing:
            p.write_text(block_re.sub("", existing).rstrip() + "\n")
            msgs.append(f"agent guidance: removed reflens block from {p}")
    return msgs


def server_command() -> list[str]:
    return [sys.executable, "-m", "reflens", "serve"]


def server_env() -> dict[str, str]:
    env: dict[str, str] = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    home = os.environ.get("REFLENS_HOME", "").strip()
    if home:
        env["REFLENS_HOME"] = home
    return env


def _opencode_config_path() -> Path:
    env = os.environ.get("OPENCODE_CONFIG", "").strip()
    if env:
        return Path(env).expanduser()
    base = os.environ.get("OPENCODE_HOME", "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".config" / "opencode"
    return root / "opencode.json"


def _claude_config_path() -> Path:
    env = os.environ.get("CLAUDE_CONFIG", "").strip()
    if env:
        return Path(env).expanduser()
    modern = Path.home() / ".claude.json"
    nested = Path.home() / ".claude" / ".claude.json"
    if modern.exists():
        return modern
    if nested.exists():
        return nested
    return modern


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not path.with_suffix(path.suffix + ".bak").exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def install_opencode(name: str = DEFAULT_SERVER_NAME) -> str:
    path = _opencode_config_path()
    data = _read_json(path)
    mcp = data.setdefault("mcp", {})
    if not isinstance(mcp, dict):
        mcp = {}
        data["mcp"] = mcp
    mcp[name] = {
        "type": "local",
        "command": server_command(),
        "environment": server_env(),
        "enabled": True,
    }
    _write_json_atomic(path, data)
    return f"OpenCode: registered '{name}' in {path}"


def install_claude(name: str = DEFAULT_SERVER_NAME) -> str:
    path = _claude_config_path()
    data = _read_json(path)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers
    cmd = server_command()
    servers[name] = {
        "type": "stdio",
        "command": cmd[0],
        "args": cmd[1:],
        "env": server_env(),
    }
    _write_json_atomic(path, data)
    return f"Claude Code: registered '{name}' in {path}"


def uninstall_opencode(name: str = DEFAULT_SERVER_NAME) -> str:
    path = _opencode_config_path()
    data = _read_json(path)
    mcp = data.get("mcp", {})
    if isinstance(mcp, dict) and name in mcp:
        del mcp[name]
        _write_json_atomic(path, data)
        return f"OpenCode: removed '{name}'"
    return f"OpenCode: '{name}' not present"


def uninstall_claude(name: str = DEFAULT_SERVER_NAME) -> str:
    path = _claude_config_path()
    data = _read_json(path)
    servers = data.get("mcpServers", {})
    if isinstance(servers, dict) and name in servers:
        del servers[name]
        _write_json_atomic(path, data)
        return f"Claude Code: removed '{name}'"
    return f"Claude Code: '{name}' not present"


_INSTALLERS = {"opencode": install_opencode, "claude": install_claude}
_UNINSTALLERS = {"opencode": uninstall_opencode, "claude": uninstall_claude}


def install(hosts: list[str], name: str = DEFAULT_SERVER_NAME) -> list[str]:
    targets = ["opencode", "claude"] if "both" in hosts or not hosts else hosts
    return [_INSTALLERS[h](name) for h in targets if h in _INSTALLERS]


def uninstall(hosts: list[str], name: str = DEFAULT_SERVER_NAME) -> list[str]:
    targets = ["opencode", "claude"] if "both" in hosts or not hosts else hosts
    return [_UNINSTALLERS[h](name) for h in targets if h in _UNINSTALLERS]
