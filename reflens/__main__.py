"""Enable `python -m reflens ...`.

This is the launch path used by MCP host configs (OpenCode / Claude Code), which
invoke `<python> -m reflens serve`. Going through the module (not a console
script on PATH) guarantees the correct interpreter and an importable package.
"""

from __future__ import annotations

from reflens.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
