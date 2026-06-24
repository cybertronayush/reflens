from __future__ import annotations

from reflens.ingest import ingest_source
from reflens.mcp import server


def test_initialize_echoes_protocol():
    resp = server.handle_message(
        {"jsonrpc": "2.0", "id": 0, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}}}
    )
    assert resp["result"]["protocolVersion"] == "2025-06-18"
    assert resp["result"]["serverInfo"]["name"] == "reflens"


def test_notification_returns_none():
    assert server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list():
    resp = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {
        "reflens_list", "reflens_map", "reflens_modules", "reflens_search",
        "reflens_read", "reflens_neighbors", "reflens_verify", "reflens_history",
    }


def test_unknown_method_errors():
    resp = server.handle_message({"jsonrpc": "2.0", "id": 9, "method": "nope/zzz"})
    assert resp["error"]["code"] == -32601


def test_tools_call_roundtrip(sample_repo):
    ingest_source("demo", str(sample_repo))
    resp = server.handle_message(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "reflens_search", "arguments": {"repo": "demo", "query": "helper"}}}
    )
    assert resp["result"]["isError"] is False
    assert "util.py" in resp["result"]["content"][0]["text"]


def test_tools_call_missing_repo_is_error():
    resp = server.handle_message(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "reflens_map", "arguments": {"repo": "does-not-exist"}}}
    )
    assert resp["result"]["isError"] is True
