# STEPS

**Part A** is what I did, in order. **Part B** is how you run it and connect it to a client.

---

## Part A: steps taken

1. **Read the existing MCP code in `customer-ops-agent`** to reuse its approach. It turned out to use the official `mcp` SDK, not a hand-written protocol, so there was no protocol module or version string to copy. I wrote the JSON-RPC layer myself (stdlib only, newline-delimited over stdio) and noted the difference in the README.
2. **Designed five tools around two ideas:** a safe read path (search with filters and a stateless cursor, get, list queues) and a guarded write path (add note, assign) that is off by default, needs an `idempotency_key`, and is audited.
3. **Wrote the data and store:** 60 deterministic fictional tickets (invented companies, no personal or payment data; one ticket carries instruction-like text on purpose), a store with filters and cursor pagination, and the two write operations.
4. **Wrote the service layer:** schema validation, the read-only gate, the idempotency ledger (replay on same key and arguments, conflict on different arguments, failed writes not remembered) and the audit log. A small JSON Schema validator avoids any dependency.
5. **Wrote the MCP server:** `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`; notifications never get a reply; unknown method, bad params, malformed JSON, batches and bad ids get the right JSON-RPC codes; tool failures are `isError` results.
6. **Wrote a minimal stdio client** (`ticketmcp/client.py`) so tests can drive the server as a real subprocess.
7. **Wrote 67 tests** in three files: protocol conformance, tool behaviour, and audit plus end-to-end over stdio. They passed on the first full run.
8. **Mutation-checked them.** A script copies the code, applies one deliberate break (10 in total), runs the suite, and requires a failure. It reports "pattern missed" if a break does not apply. All 10 were caught on the first run.
9. **Self-review caught two things before commit:** a leftover dead conditional in the client's subprocess arguments, and a meaningless always-true assertion in the retry test, which I replaced with a real check that the replayed note keeps the original note id.
10. **Added CI** (copied from `agent-eval-harness`: Python 3.11 and 3.13), `.gitignore`, `.gitattributes` (LF), MIT license, README.
11. **Committed locally.** Pushing to GitHub is done separately, and the CI result gets recorded once it has run.

Problems hit: none blocking. I chose to make a failed write retryable (not cached in the ledger) after thinking through a client that retries after a transient `not_found`, and I made the conflict case an explicit error rather than silently returning the first result.

---

## Part B: steps for you

### Run it
1. Install Python 3.11 or newer. Nothing else is needed to run the server.
2. Clone the repo and `cd` into it.
3. Start it read-only: `python -m ticketmcp`. It waits for JSON-RPC messages on stdin, so it will look idle. Press Ctrl+C or close stdin to stop it.
4. Start with writes enabled: `python -m ticketmcp --allow-writes --audit-log audit.jsonl`.
5. Try it by hand: paste these lines into the running server, one per line:
   ```
   {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"hand","version":"0"}}}
   {"jsonrpc":"2.0","method":"notifications/initialized"}
   {"jsonrpc":"2.0","id":2,"method":"tools/list"}
   {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_tickets","arguments":{"queue":"billing","limit":3}}}
   ```
6. Run the tests: `python -m pip install pytest`, then `python -m pytest`. Expect `67 passed`.

### Connect it to a client (untested)
1. **Claude Code:** from the repo folder run `claude mcp add ticket-triage -- python -m ticketmcp --audit-log ticket-audit.jsonl`, then check it with `claude mcp list`.
2. **Claude Desktop:** edit `claude_desktop_config.json` and add the `mcpServers` entry shown in the README (set `cwd` to where you cloned the repo), then restart Claude Desktop.
3. To let the model write, add `--allow-writes` to the arguments. Ask it to add a note, then ask again with the same request and check that `ticket-audit.jsonl` shows the second call as `"replayed": true`.
4. If the client reports a protocol error, send me the message it shows (or the server's stderr), since the protocol layer has only been tested against my own client.
