# Architecture

```
MCP client (agent host)
   |  newline-delimited JSON-RPC 2.0 over stdin/stdout
   v
cli.serve  -->  protocol.Server.handle(msg)  -->  registry.Registry.call(name, args)  -->  tool function
 (transport)     (lifecycle, routing, errors)      (schema validation, error mapping)        (business logic)
```

| Module | Role |
|---|---|
| `cli.py` | Transport only: read a line, decode, dispatch, write one line. Enforces a message size cap. |
| `protocol.py` | MCP lifecycle and routing. Pure function `handle(msg) -> response or None`, so it needs no process to test. |
| `registry.py` | Tool metadata, JSON-Schema-style validation, and the mapping from exceptions to results. |
| `tools.py` | The four operations tools and their guards. |

## Protocol behaviour implemented

- `initialize` negotiates the protocol version: it echoes the client's version if supported, otherwise offers the newest supported one. It advertises the `tools` capability.
- `notifications/initialized` flips the server to ready. Notifications never receive a response, even if unknown.
- Requests other than `initialize` and `ping` before initialization return an error.
- `tools/list` returns name, description, input schema and annotations.
- `tools/call` returns `content` plus `isError`.
- Error codes follow JSON-RPC: -32700 parse, -32600 invalid request, -32601 unknown method, -32602 invalid params, -32603 internal.

## Decisions

**Two error channels, on purpose.** A malformed request is a protocol problem. A well-formed call that fails (unknown KPI, bad argument value) is a tool outcome the model should read and react to. Mixing them makes agents brittle, because the model never sees protocol errors.

**Approvals as data, not actions.** The server exposes a way to ask for something risky, never to do it. The audit trail is a table of pending requests that a human works through.

**Validation lives in the registry, not in each tool.** Every tool gets the same checks for free, and there is one place to harden.

**Transport kept separate from protocol.** Swapping stdio for HTTP would touch `cli.py` only.

## Limitations and next steps

- stdio transport only. A remote deployment needs Streamable HTTP with authentication and per-client authorisation.
- Validation covers the JSON Schema subset used here, not the full specification.
- No resources or prompts capabilities, and no progress or cancellation support.
- The approval table has no worker. In production, approvals would flow into the workflow engine (see `agentflow-hitl`) with identity-bound approvers.
- Rate limiting and per-tool audit logging would be needed before exposing it to untrusted agents.
