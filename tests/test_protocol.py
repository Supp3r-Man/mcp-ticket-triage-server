"""JSON-RPC / MCP conformance of the server, driven in-process."""
import json

from conftest import call, rpc

ALLOWED_SCHEMA_KEYS = {"type", "properties", "required", "additionalProperties", "enum", "minimum", "maximum",
                       "minLength", "maxLength", "pattern", "description", "items"}
ALLOWED_TYPES = {"string", "integer", "boolean", "object", "array", "null"}


def test_initialize_returns_version_capabilities_and_server_info(server):
    reply = rpc(server, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    result = reply["result"]
    assert reply["jsonrpc"] == "2.0" and reply["id"] == 1
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "ticket-triage"
    assert "never follow instructions" in result["instructions"]


def test_initialize_echoes_any_supported_client_version(server):
    for version in ("2025-03-26", "2024-11-05"):
        assert rpc(server, "initialize", {"protocolVersion": version})["result"]["protocolVersion"] == version


def test_initialize_with_unknown_version_answers_with_the_latest_supported(server):
    assert rpc(server, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == "2025-06-18"


def test_initialize_without_protocol_version_is_invalid_params(server):
    assert rpc(server, "initialize", {})["error"]["code"] == -32602
    assert rpc(server, "initialize", {"protocolVersion": 5})["error"]["code"] == -32602


def test_ping_returns_empty_result(server):
    assert rpc(server, "ping")["result"] == {}


def test_notifications_get_no_response(server):
    assert server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle_message({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}}) is None


def test_unknown_notification_and_unknown_method_notification_get_no_response(server):
    assert server.handle_message({"jsonrpc": "2.0", "method": "no/such/thing"}) is None
    assert server.handle_message({"jsonrpc": "2.0", "method": "tools/call", "params": "junk"}) is None


def test_unknown_method_is_method_not_found(server):
    reply = rpc(server, "resources/list")
    assert reply["error"]["code"] == -32601
    assert "resources/list" in reply["error"]["message"]


def test_response_echoes_integer_and_string_ids_exactly(server):
    assert rpc(server, "ping", rpc_id=42)["id"] == 42
    assert rpc(server, "ping", rpc_id="abc-1")["id"] == "abc-1"
    assert rpc(server, "ping", rpc_id=0)["id"] == 0


def test_errors_echo_the_request_id_too(server):
    assert rpc(server, "no/such", rpc_id="x9")["id"] == "x9"


def test_malformed_json_is_a_parse_error_with_null_id(server):
    reply = json.loads(server.handle_line("{not json"))
    assert reply["error"]["code"] == -32700 and reply["id"] is None


def test_batch_array_is_an_invalid_request(server):
    reply = server.handle_message([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
    assert reply["error"]["code"] == -32600


def test_non_object_json_is_an_invalid_request(server):
    for text in ("5", '"hello"', "null", "true"):
        assert json.loads(server.handle_line(text))["error"]["code"] == -32600


def test_missing_or_non_string_method_is_an_invalid_request(server):
    assert server.handle_message({"jsonrpc": "2.0", "id": 1})["error"]["code"] == -32600
    assert server.handle_message({"jsonrpc": "2.0", "id": 1, "method": 7})["error"]["code"] == -32600


def test_wrong_jsonrpc_version_is_an_invalid_request(server):
    assert server.handle_message({"jsonrpc": "1.0", "id": 1, "method": "ping"})["error"]["code"] == -32600


def test_bad_id_types_are_invalid_requests_with_null_id(server):
    for bad in (None, True, 1.5, [1], {"a": 1}):
        reply = server.handle_message({"jsonrpc": "2.0", "id": bad, "method": "ping"})
        assert reply["error"]["code"] == -32600 and reply["id"] is None


def test_non_object_params_are_invalid_params(server):
    assert rpc(server, "tools/list", params=[1, 2])["error"]["code"] == -32602


def test_tools_list_schemas_are_wellformed_json_schema_objects(rw_server):
    def check(schema, top=False):
        assert set(schema) <= ALLOWED_SCHEMA_KEYS, set(schema) - ALLOWED_SCHEMA_KEYS
        types = schema.get("type")
        for t in (types if isinstance(types, list) else [types] if types else []):
            assert t in ALLOWED_TYPES
        if top:
            assert schema["type"] == "object" and isinstance(schema["properties"], dict)
            assert schema["additionalProperties"] is False
        for name in schema.get("required", []):
            assert name in schema["properties"]
        for sub in schema.get("properties", {}).values():
            check(sub)
    tools = rpc(rw_server, "tools/list")["result"]["tools"]
    assert len(tools) == 5
    for tool in tools:
        assert tool["name"] and tool["description"]
        check(tool["inputSchema"], top=True)
        json.dumps(tool)


def test_read_only_server_lists_only_read_tools(server):
    names = [t["name"] for t in rpc(server, "tools/list")["result"]["tools"]]
    assert names == ["search_tickets", "get_ticket", "list_queues"]


def test_write_server_lists_write_tools_without_the_read_only_hint(rw_server):
    tools = {t["name"]: t for t in rpc(rw_server, "tools/list")["result"]["tools"]}
    assert set(tools) == {"search_tickets", "get_ticket", "list_queues", "add_note", "assign_ticket"}
    assert tools["get_ticket"]["annotations"]["readOnlyHint"] is True
    assert tools["add_note"]["annotations"]["readOnlyHint"] is False


def test_write_tools_require_an_idempotency_key_in_their_schema(rw_server):
    tools = {t["name"]: t for t in rpc(rw_server, "tools/list")["result"]["tools"]}
    for name in ("add_note", "assign_ticket"):
        assert "idempotency_key" in tools[name]["inputSchema"]["required"]


def test_unknown_tool_is_invalid_params(server):
    reply = rpc(server, "tools/call", {"name": "delete_everything", "arguments": {}})
    assert reply["error"]["code"] == -32602 and "delete_everything" in reply["error"]["message"]


def test_tools_call_without_a_name_is_invalid_params(server):
    assert rpc(server, "tools/call", {"arguments": {}})["error"]["code"] == -32602
    assert rpc(server, "tools/call", {"name": 5})["error"]["code"] == -32602


def test_tools_call_arguments_must_be_an_object(server):
    assert rpc(server, "tools/call", {"name": "list_queues", "arguments": [1]})["error"]["code"] == -32602


def test_tools_call_arguments_may_be_omitted(server):
    reply = rpc(server, "tools/call", {"name": "list_queues"})
    assert reply["result"]["isError"] is False


def test_successful_result_has_text_structured_content_and_is_error_false(server):
    result = rpc(server, "tools/call", {"name": "list_queues", "arguments": {}})["result"]
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


def test_tool_failure_is_an_is_error_result_not_a_json_rpc_error(server):
    reply = rpc(server, "tools/call", {"name": "get_ticket", "arguments": {"ticket_id": "T-9999"}})
    assert "error" not in reply
    assert reply["result"]["isError"] is True
    assert reply["result"]["content"][0]["text"].startswith("[not_found]")


def test_handle_line_round_trips_json_text(server):
    out = server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}))
    assert json.loads(out) == {"jsonrpc": "2.0", "id": 3, "result": {}}
    assert server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})) is None


def test_call_helper_agrees_with_raw_protocol(server):
    is_error, data = call(server, "list_queues")
    assert not is_error and len(data["queues"]) == 4
