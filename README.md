# ops-mcp-server

A **Model Context Protocol (MCP) server** that gives an AI agent safe, auditable access to operations data and actions: KPIs, team capacity, runbooks, and approval requests.

The protocol layer is implemented from scratch (JSON-RPC 2.0 over stdio, standard library only) so every design decision is visible. The interesting part is not the plumbing but the **tool-safety design**: what an agent is allowed to read, what it is only allowed to *request*, and how bad input is handled.

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `kpi_status` | read-only | Value, target and red/amber/green status for one or all KPIs |
| `capacity_check` | read-only | Assigned vs available hours per team, flags HIGH / OVER |
| `search_runbooks` | read-only | Ranked search over operational runbooks |
| `request_approval` | write (limited) | Creates a **pending** approval request. It never performs the business action |

Each tool advertises a JSON Schema and MCP annotations (`readOnlyHint`, `destructiveHint`, ...) so a client can decide what to auto-approve.

## Run it

```bash
pip install -e .
opsmcp --data data --db approvals.db
```

Smoke test by hand (one JSON message per line):

```bash
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
 '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"capacity_check","arguments":{}}}' \
 | opsmcp --data data --db :memory:
```

Connect it to an MCP client such as Claude Desktop by adding this to its config:

```json
{
  "mcpServers": {
    "ops": { "command": "opsmcp", "args": ["--data", "/path/to/data", "--db", "/path/to/approvals.db"] }
  }
}
```

## Safety design

1. **Read tools cannot change anything.** They only read files from one data directory.
2. **The write tool only requests.** `request_approval` records a pending row. Approving and executing happen elsewhere, by a human (this is the same autonomy idea as in `agentflow-hitl`).
3. **Arguments are untrusted.** Strict validation: required fields, types (a boolean is not an integer), enums, bounds, length limits, and **unexpected fields are rejected**.
4. **Tool errors are returned to the model, not thrown.** Bad input gives `isError: true` with a helpful message ("no KPI named x; available: ..."), so the model can recover. Protocol errors (unknown tool, malformed message) use JSON-RPC error codes.
5. **No internals leak.** Unexpected exceptions return only the exception type.
6. **Path confinement.** File access is resolved and checked to stay inside the data directory.
7. **stdout is the protocol.** Logging goes to stderr only; a stray print would corrupt the stream.
8. **The server survives garbage.** An invalid JSON line returns a parse error and the loop continues.

## Tests

23 tests, including a subprocess test that speaks to the server over real stdio.

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the protocol notes and limitations.

## License

MIT
