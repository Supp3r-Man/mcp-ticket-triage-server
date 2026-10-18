# mcp-ticket-triage-server

![ci](https://github.com/Supp3r-Man/mcp-ticket-triage-server/actions/workflows/ci.yml/badge.svg)

A **Model Context Protocol (MCP) server over stdio** for a synthetic support-ticket store, written with the Python standard library only. It lets an MCP client search tickets with cursor pagination, read them, and (only when started with `--allow-writes`) add notes and assign tickets, with **idempotency keys on every write** and a **JSONL audit log of every call**.

> **What is verified, and what is not.** 67 tests pass locally on Python 3.13 (and in GitHub Actions on Linux with Python 3.11 and 3.13, stdlib only). They cover protocol conformance (handshake, error codes, notifications, malformed input, id echo, tool schemas), every tool, the read-only gate, idempotency and the audit log, and drive the server through a **real subprocess over stdio with an in-repo client** (`ticketmcp/client.py`). I broke the code on purpose 10 ways (replay, read-only gate, cursor, id echo, notification replies, error code, page limit, case folding, audit of failures, conflict check) and the tests failed every time. **It has never been connected to a real MCP client** (Claude Desktop, Claude Code or the official SDK); the in-repo client is mine and only knows what I implemented, so a client that expects something I got wrong would not be caught. The JSON-RPC layer is hand-written (the sibling repo `customer-ops-agent` uses the official SDK instead), and the supported protocol versions (`2025-06-18`, `2025-03-26`, `2024-11-05`) are from my memory of the spec. The ticket data is synthetic and fictional (a test scans it for card-number, SSN and secret patterns).

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `search_tickets` | read | Filters `queue`, `status`, `priority`, `assignee` (a name or `unassigned`), `query` (text in subject/body); `limit` 1-50 (default 10); `cursor` for the next page. Returns summaries, `next_cursor` and `total_matches`. |
| `get_ticket` | read | One ticket in full, with body and notes. |
| `list_queues` | read | Per-queue counts by status and number unassigned. |
| `add_note` | **write** | Appends a note. Needs `idempotency_key`. |
| `assign_ticket` | **write** | Assigns to one of four agents, or `null` to unassign. Needs `idempotency_key`. |

- **Read-only by default.** Write tools are not even listed unless the server starts with `--allow-writes`; calling one anyway returns an error result `[writes_disabled]`.
- **Idempotency.** The same `idempotency_key` with the same arguments returns the original result and applies nothing twice (the audit log marks it `replayed`). The same key with different arguments (or another tool) is `[idempotency_conflict]`. A write that failed is not remembered, so the key can be retried. The key ledger lives in memory: it does not survive a restart.
- **Errors.** A tool that runs and fails (bad arguments, not found, writes disabled, ...) is a normal result with `isError: true` and a text like `[not_found] no ticket T-9999`. Protocol problems are JSON-RPC errors: `-32700` parse error, `-32600` invalid request (including batches), `-32601` unknown method, `-32602` bad params or unknown tool.
- **Audit log.** One JSON line per call (`ts`, `tool`, `args`, `outcome`, `error_code`, `duration_ms`, `replayed`, `writes_enabled`). Note text is cut to 80 characters plus its length.
- **Ticket text is data.** One synthetic ticket contains instruction-like text on purpose. The server returns it untouched and never acts on it; the server `instructions` and tool descriptions tell the model the same.

## Run it

```
python -m ticketmcp                              # read-only, audit log in ./ticket-audit.jsonl
python -m ticketmcp --allow-writes --audit-log audit.jsonl
python -m pytest                                 # needs pytest
```

stdout carries protocol messages only; the start-up line goes to stderr.

## Connect it to a client (untested)

These snippets follow the usual shape of MCP client configs. **I have not tried them against a real client**, so treat them as a starting point.

Claude Code:

```
claude mcp add ticket-triage -- python -m ticketmcp --audit-log ticket-audit.jsonl
```

(run it from the repository folder so `python -m ticketmcp` can find the package)

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "ticket-triage": {
      "command": "python",
      "args": ["-m", "ticketmcp", "--audit-log", "ticket-audit.jsonl"],
      "cwd": "C:\\path\\to\\mcp-ticket-triage-server"
    }
  }
}
```

Add `"--allow-writes"` to `args` to enable the write tools.

## Layout

```
ticketmcp/server.py   JSON-RPC 2.0 / MCP handling and the stdio loop
ticketmcp/tools.py    tool definitions + Service (validation, read-only gate, idempotency, audit)
ticketmcp/store.py    in-memory store: search, cursor pagination, notes, assignment
ticketmcp/schema.py   small JSON Schema validator for the tool arguments
ticketmcp/audit.py    JSONL audit log
ticketmcp/data.py     deterministic synthetic tickets
ticketmcp/client.py   minimal stdio client used by the tests
tests/                67 tests
STEPS.md              what I did, in order, and how to run and connect it
```

## Known gaps

- Never run against a real MCP client; protocol details are from memory of the spec.
- No lifecycle enforcement: requests are answered even before `initialize`/`initialized`.
- Tools only; no resources, prompts, progress, cancellation or `listChanged` notifications.
- The schema validator supports only the keywords the tool schemas use.
- State (tickets, notes, idempotency keys) is in memory and resets on restart; the audit log is append-only and never rotated.
- Single connection, synchronous, no authentication: it trusts whoever is on the other end of stdio.
- The audit file path is not locked, so two server processes writing the same file could interleave lines.

## License

MIT
