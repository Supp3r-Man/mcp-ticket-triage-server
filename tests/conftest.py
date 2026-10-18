import json

import pytest

from ticketmcp.audit import AuditLog
from ticketmcp.server import McpServer
from ticketmcp.store import TicketStore
from ticketmcp.tools import Service


@pytest.fixture
def make_server():
    def build(allow_writes: bool = False, audit_path: str | None = None) -> McpServer:
        service = Service(TicketStore(clock=lambda: "2026-10-01T00:00:00Z"), allow_writes=allow_writes, audit=AuditLog(audit_path))
        return McpServer(service)
    return build


@pytest.fixture
def server(make_server):
    return make_server()


@pytest.fixture
def rw_server(make_server):
    return make_server(allow_writes=True)


def rpc(srv: McpServer, method: str, params=None, rpc_id=1):
    message = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        message["params"] = params
    return srv.handle_message(message)


def call(srv: McpServer, name: str, arguments=None, rpc_id=1):
    """tools/call that returns (is_error, structured_or_text)."""
    reply = rpc(srv, "tools/call", {"name": name, "arguments": arguments or {}}, rpc_id)
    result = reply["result"]
    if result["isError"]:
        return True, result["content"][0]["text"]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    return False, result["structuredContent"]
