"""A minimal MCP client over stdio, used by the tests and the demo. It is not a general MCP client."""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any


class McpClientError(RuntimeError):
    pass


class StdioClient:
    def __init__(self, args: list[str] | None = None, timeout: float = 15.0) -> None:
        self.timeout = timeout
        # cwd is the repo root so `-m ticketmcp` resolves no matter where the caller runs from.
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "ticketmcp", *(args or [])],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        self._lines: "queue.Queue[str | None]" = queue.Queue()
        self.raw_stdout: list[str] = []
        self._next_id = 0
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            self.raw_stdout.append(line)
            self._lines.put(line)
        self._lines.put(None)

    def send_raw(self, text: str) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(text + "\n")
        self._proc.stdin.flush()

    def read(self) -> dict[str, Any]:
        try:
            line = self._lines.get(timeout=self.timeout)
        except queue.Empty:
            raise McpClientError("timed out waiting for the server") from None
        if line is None:
            raise McpClientError("server closed stdout")
        return json.loads(line)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send_raw(json.dumps(message))
        reply = self.read()
        if reply.get("id") != self._next_id:
            raise McpClientError(f"reply id {reply.get('id')!r} does not match request id {self._next_id}")
        return reply

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.send_raw(json.dumps(message))

    def initialize(self) -> dict[str, Any]:
        reply = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                            "clientInfo": {"name": "ticketmcp-test-client", "version": "0"}})
        self.notify("notifications/initialized")
        return reply["result"]

    def list_tools(self) -> list[dict[str, Any]]:
        return self.request("tools/list")["result"]["tools"]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Returns the tools/call result (with isError). Raises McpClientError on a JSON-RPC error."""
        reply = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        if "error" in reply:
            raise McpClientError(f"{reply['error']['code']}: {reply['error']['message']}")
        return reply["result"]

    def close(self) -> int:
        assert self._proc.stdin is not None
        self._proc.stdin.close()
        try:
            code = self._proc.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            code = -1
        for stream in (self._proc.stdout, self._proc.stderr):
            if stream:
                stream.close()
        return code

    def __enter__(self) -> "StdioClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
