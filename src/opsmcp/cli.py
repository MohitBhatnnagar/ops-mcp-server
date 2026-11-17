"""stdio transport: one JSON-RPC message per line on stdin, responses on stdout.

Logs go to stderr only. Anything else written to stdout would corrupt the protocol stream.
"""
from __future__ import annotations

import argparse
import json
import sys

from .protocol import PARSE_ERROR, Server, error
from .tools import build_registry

MAX_LINE = 1_000_000  # refuse absurdly large messages


def serve(server: Server, stdin=sys.stdin, stdout=sys.stdout) -> None:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        if len(line) > MAX_LINE:
            resp = error(None, PARSE_ERROR, "message too large")
        else:
            try:
                resp = server.handle(json.loads(line))
            except json.JSONDecodeError:
                resp = error(None, PARSE_ERROR, "invalid JSON")
        if resp is not None:
            stdout.write(json.dumps(resp, separators=(",", ":")) + "\n")
            stdout.flush()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="opsmcp", description="Operations MCP server (stdio)")
    p.add_argument("--data", default="data", help="directory with kpis.json, teams.json, runbooks/")
    p.add_argument("--db", default="approvals.db", help="SQLite file for approval requests")
    a = p.parse_args(argv)
    print("opsmcp ready on stdio", file=sys.stderr)
    serve(Server("opsmcp", "0.1.0", build_registry(a.data, a.db)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
