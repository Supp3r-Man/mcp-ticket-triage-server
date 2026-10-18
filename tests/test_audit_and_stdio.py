"""Audit log behaviour (in-process) and end-to-end behaviour through a real subprocess over stdio."""
import json
import re

import pytest

from conftest import call
from ticketmcp.client import McpClientError, StdioClient


# ---- audit ----

def test_every_call_is_audited_ok_or_error(server):
    call(server, "list_queues")
    call(server, "get_ticket", {"ticket_id": "T-9999"})
    entries = list(server.service.audit.entries)
    assert [(e["tool"], e["outcome"], e["error_code"]) for e in entries] == [("list_queues", "ok", None), ("get_ticket", "error", "not_found")]
    assert all(e["writes_enabled"] is False and e["duration_ms"] >= 0 for e in entries)


def test_refused_writes_and_invalid_arguments_are_audited(server):
    call(server, "add_note", {"ticket_id": "T-1001", "text": "hi", "idempotency_key": "audit-key-001"})
    call(server, "search_tickets", {"limit": 999})
    codes = [e["error_code"] for e in server.service.audit.entries]
    assert codes == ["writes_disabled", "invalid_arguments"]


def test_replayed_writes_are_marked_as_replays_in_the_audit_log(rw_server):
    args = {"ticket_id": "T-1001", "text": "once", "idempotency_key": "audit-key-002"}
    call(rw_server, "add_note", args)
    call(rw_server, "add_note", args)
    assert [e["replayed"] for e in rw_server.service.audit.entries] == [False, True]


def test_long_note_text_is_truncated_in_the_audit_log_but_the_length_is_kept(rw_server):
    call(rw_server, "add_note", {"ticket_id": "T-1001", "text": "z" * 500, "idempotency_key": "audit-key-003"})
    logged = rw_server.service.audit.entries[-1]["args"]
    assert len(logged["text"]) <= 83 and logged["text"].endswith("...") and logged["text_len"] == 500


def test_audit_file_gets_one_json_line_per_call(make_server, tmp_path):
    path = tmp_path / "audit.jsonl"
    srv = make_server(audit_path=str(path))
    call(srv, "list_queues")
    call(srv, "get_ticket", {"ticket_id": "T-1001"})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and [json.loads(l)["tool"] for l in lines] == ["list_queues", "get_ticket"]


# ---- real subprocess over stdio ----

def test_stdio_handshake_list_and_read_calls(tmp_path):
    with StdioClient(["--audit-log", str(tmp_path / "a.jsonl")]) as client:
        info = client.initialize()
        assert info["serverInfo"]["name"] == "ticket-triage"
        assert [t["name"] for t in client.list_tools()] == ["search_tickets", "get_ticket", "list_queues"]
        result = client.call_tool("search_tickets", {"queue": "billing", "limit": 2})
        assert not result["isError"] and len(result["structuredContent"]["tickets"]) == 2


def test_stdio_read_only_by_default_and_writes_with_flag(tmp_path):
    key = "stdio-key-0001"
    with StdioClient(["--audit-log", str(tmp_path / "ro.jsonl")]) as client:
        client.initialize()
        refused = client.call_tool("add_note", {"ticket_id": "T-1001", "text": "x", "idempotency_key": key})
        assert refused["isError"] and "[writes_disabled]" in refused["content"][0]["text"]
    with StdioClient(["--allow-writes", "--audit-log", str(tmp_path / "rw.jsonl")]) as client:
        client.initialize()
        assert len(client.list_tools()) == 5
        first = client.call_tool("add_note", {"ticket_id": "T-1001", "text": "x", "idempotency_key": key})
        again = client.call_tool("add_note", {"ticket_id": "T-1001", "text": "x", "idempotency_key": key})
        assert first["structuredContent"] == again["structuredContent"]
        notes = client.call_tool("get_ticket", {"ticket_id": "T-1001"})["structuredContent"]["notes"]
        assert [n["text"] for n in notes].count("x") == 1


def test_stdout_carries_only_json_rpc_messages(tmp_path):
    with StdioClient(["--audit-log", str(tmp_path / "o.jsonl")]) as client:
        client.initialize()
        client.list_tools()
        client.call_tool("list_queues")
        client.send_raw("{broken")
        client.read()
        client.close()
        assert client.raw_stdout
        for line in client.raw_stdout:
            message = json.loads(line)
            assert message["jsonrpc"] == "2.0" and ("result" in message or "error" in message)


def test_stdio_malformed_line_does_not_stop_the_server(tmp_path):
    with StdioClient(["--audit-log", str(tmp_path / "m.jsonl")]) as client:
        client.send_raw("this is not json")
        assert client.read()["error"]["code"] == -32700
        client.send_raw("")  # blank lines are ignored
        client.initialize()
        assert client.call_tool("list_queues")["structuredContent"]["queues"]


def test_stdio_unknown_tool_surfaces_as_a_json_rpc_error(tmp_path):
    with StdioClient(["--audit-log", str(tmp_path / "u.jsonl")]) as client:
        client.initialize()
        with pytest.raises(McpClientError, match="-32602"):
            client.call_tool("nope")


def test_stdio_exits_cleanly_when_stdin_closes_and_writes_the_audit_file(tmp_path):
    path = tmp_path / "e.jsonl"
    client = StdioClient(["--audit-log", str(path)])
    client.initialize()
    client.call_tool("list_queues")
    assert client.close() == 0
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["tool"] == "list_queues"


def test_stdio_ids_are_echoed_for_string_ids_too(tmp_path):
    with StdioClient(["--audit-log", str(tmp_path / "i.jsonl")]) as client:
        client.send_raw(json.dumps({"jsonrpc": "2.0", "id": "req-α", "method": "ping"}))
        assert client.read() == {"jsonrpc": "2.0", "id": "req-α", "result": {}}


def test_server_announces_itself_on_stderr_not_stdout(tmp_path):
    client = StdioClient(["--audit-log", str(tmp_path / "s.jsonl")])
    client.initialize()
    client.close()
    assert all(not re.search(r"listening on stdio", line) for line in client.raw_stdout)
