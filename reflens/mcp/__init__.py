"""MCP stdio server (stdlib-only JSON-RPC 2.0). No external SDK dependency."""

from __future__ import annotations

from .server import serve

__all__ = ["serve"]
