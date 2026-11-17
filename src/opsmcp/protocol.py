"""JSON-RPC 2.0 message handling for the Model Context Protocol (MCP).

Transport is newline-delimited JSON over stdio. This module is transport-free:
`handle(message)` takes a decoded message and returns a response dict, or None
for notifications, so it can be unit-tested without spawning a process.
"""
from __future__ import annotations

from typing import Any, Callable

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL = -32700, -32600, -32601, -32602, -32603


def error(id_: Any, code: int, message: str, data: Any = None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": err}


def result(id_: Any, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": payload}


class Server:
    def __init__(self, name: str, version: str, registry):
        self.name, self.version, self.registry = name, version, registry
        self.initialized = False
        self._methods: dict[str, Callable[[dict], dict]] = {
            "initialize": self._initialize,
            "ping": lambda p: {},
            "tools/list": self._tools_list,
            "tools/call": self._tools_call,
        }

    def handle(self, msg: Any) -> dict | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
        method, id_ = msg.get("method"), msg.get("id")
        if not isinstance(method, str):
            return error(id_, INVALID_REQUEST, "missing method")
        is_notification = "id" not in msg
        if is_notification:
            if method == "notifications/initialized":
                self.initialized = True
            return None  # notifications never get a response, even for unknown methods
        fn = self._methods.get(method)
        if fn is None:
            return error(id_, METHOD_NOT_FOUND, f"unknown method: {method}")
        if method not in ("initialize", "ping") and not self.initialized:
            return error(id_, INVALID_REQUEST, "server not initialized")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            return error(id_, INVALID_PARAMS, "params must be an object")
        try:
            return result(id_, fn(params))
        except ProtocolError as exc:
            return error(id_, exc.code, str(exc))
        except Exception as exc:  # noqa: BLE001 - never crash the transport loop
            return error(id_, INTERNAL, f"internal error: {type(exc).__name__}")

    # -- methods -----------------------------------------------------------

    def _initialize(self, params: dict) -> dict:
        wanted = params.get("protocolVersion")
        version = wanted if wanted in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return {"protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": self.version}}

    def _tools_list(self, params: dict) -> dict:
        return {"tools": self.registry.describe()}

    def _tools_call(self, params: dict) -> dict:
        name, args = params.get("name"), params.get("arguments") or {}
        if not isinstance(name, str) or not self.registry.has(name):
            raise ProtocolError(INVALID_PARAMS, f"unknown tool: {name!r}")
        if not isinstance(args, dict):
            raise ProtocolError(INVALID_PARAMS, "arguments must be an object")
        # Tool-level failures are reported IN the result (isError), not as protocol
        # errors, so the model can see the message and recover.
        return self.registry.call(name, args)


class ProtocolError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
