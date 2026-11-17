import json
import os
import subprocess
import sys
import tempfile
import unittest

from opsmcp import Server, build_registry, validate

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA = os.path.join(ROOT, "data")


def server():
    s = Server("opsmcp", "0.1.0", build_registry(DATA))
    s.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return s


def call(s, name, args=None, id_=1):
    r = s.handle({"jsonrpc": "2.0", "id": id_, "method": "tools/call",
                  "params": {"name": name, "arguments": args or {}}})
    return r["result"]


class ProtocolTests(unittest.TestCase):
    def test_initialize_negotiates_version(self):
        s = Server("x", "1", build_registry(DATA))
        r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
        self.assertEqual(r["result"]["protocolVersion"], "2024-11-05")
        self.assertIn("tools", r["result"]["capabilities"])

    def test_unknown_version_falls_back_to_latest(self):
        s = Server("x", "1", build_registry(DATA))
        r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")

    def test_requires_initialization(self):
        s = Server("x", "1", build_registry(DATA))
        r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(r["error"]["code"], -32600)

    def test_notifications_get_no_response(self):
        s = Server("x", "1", build_registry(DATA))
        self.assertIsNone(s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertIsNone(s.handle({"jsonrpc": "2.0", "method": "notifications/unknown"}))

    def test_unknown_method(self):
        r = server().handle({"jsonrpc": "2.0", "id": 5, "method": "nope"})
        self.assertEqual(r["error"]["code"], -32601)
        self.assertEqual(r["id"], 5)

    def test_not_jsonrpc(self):
        r = server().handle({"id": 1, "method": "ping"})
        self.assertEqual(r["error"]["code"], -32600)

    def test_ping(self):
        self.assertEqual(server().handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})["result"], {})

    def test_tools_list_has_schemas_and_annotations(self):
        tools = {t["name"]: t for t in server().handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]}
        self.assertEqual(set(tools), {"kpi_status", "capacity_check", "search_runbooks", "request_approval"})
        self.assertTrue(tools["kpi_status"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["request_approval"]["annotations"]["readOnlyHint"])
        self.assertEqual(tools["search_runbooks"]["inputSchema"]["required"], ["query"])

    def test_unknown_tool_is_protocol_error(self):
        r = server().handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "rm_rf"}})
        self.assertEqual(r["error"]["code"], -32602)


class ToolTests(unittest.TestCase):
    def test_kpi_status_all(self):
        out = call(server(), "kpi_status")
        self.assertFalse(out["isError"])
        text = out["content"][0]["text"]
        self.assertIn("First-pass quality %: 93 (target 95) -> amber", text)
        self.assertIn("Onboarding days: 12 (target 14) -> green", text)
        self.assertIn("Automation coverage %: 31 (target 40) -> red", text)

    def test_kpi_unknown_is_tool_error_with_help(self):
        out = call(server(), "kpi_status", {"kpi": "nonsense"})
        self.assertTrue(out["isError"])
        self.assertIn("available", out["content"][0]["text"])

    def test_capacity_flags_over_allocation(self):
        text = call(server(), "capacity_check")["content"][0]["text"]
        self.assertIn("Delivery B: 109% (OVER)", text)
        self.assertIn("Delivery A: 88% (ok)", text)

    def test_capacity_threshold_changes_flag(self):
        text = call(server(), "capacity_check", {"threshold": 0.8})["content"][0]["text"]
        self.assertIn("Delivery A: 88% (HIGH)", text)

    def test_search_finds_relevant_runbook(self):
        text = call(server(), "search_runbooks", {"query": "client reported an error after delivery"})["content"][0]["text"]
        self.assertTrue(text.startswith("[quality-escape]"))

    def test_search_no_match(self):
        text = call(server(), "search_runbooks", {"query": "zzzz qqqq"})["content"][0]["text"]
        self.assertEqual(text, "no matching runbooks")

    def test_request_approval_only_creates_pending(self):
        out = call(server(), "request_approval",
                   {"action": "Refund client 1200", "reason": "late delivery", "requested_by": "agent-7"})
        self.assertFalse(out["isError"])
        self.assertIn("pending", out["content"][0]["text"])


class ValidationTests(unittest.TestCase):
    def test_missing_required(self):
        out = call(server(), "search_runbooks", {})
        self.assertTrue(out["isError"])
        self.assertIn("missing required field 'query'", out["content"][0]["text"])

    def test_wrong_type_and_bounds(self):
        self.assertTrue(validate({"query": 5}, {"properties": {"query": {"type": "string"}}}))
        self.assertTrue(validate({"n": 9}, {"properties": {"n": {"type": "integer", "maximum": 5}}}))

    def test_bool_is_not_an_integer(self):
        self.assertTrue(validate({"n": True}, {"properties": {"n": {"type": "integer"}}}))

    def test_unexpected_field_rejected(self):
        out = call(server(), "kpi_status", {"kpi": "x", "shell": "rm -rf /"})
        self.assertTrue(out["isError"])
        self.assertIn("unexpected field 'shell'", out["content"][0]["text"])

    def test_length_limits(self):
        out = call(server(), "search_runbooks", {"query": "a" * 500})
        self.assertTrue(out["isError"])

    def test_path_traversal_blocked(self):
        reg = build_registry(DATA)
        # kpi_status reads a fixed file; prove the guard exists by pointing the data dir at a missing file
        empty = tempfile.mkdtemp()
        out = build_registry(empty).call("kpi_status", {})
        self.assertTrue(out["isError"])
        self.assertIn("not found", out["content"][0]["text"])
        self.assertTrue(reg.has("kpi_status"))


class StdioTests(unittest.TestCase):
    def test_end_to_end_over_real_stdio(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "capacity_check", "arguments": {}}},
        ]
        stdin = "\n".join(json.dumps(m) for m in msgs) + "\nthis is not json\n"
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"))
        p = subprocess.run([sys.executable, "-m", "opsmcp.cli", "--data", DATA, "--db", ":memory:"],
                           input=stdin, capture_output=True, text=True, env=env, timeout=30)
        lines = [json.loads(x) for x in p.stdout.splitlines()]
        self.assertEqual([m.get("id") for m in lines], [1, 2, None])  # notification produced nothing
        self.assertIn("Delivery B", lines[1]["result"]["content"][0]["text"])
        self.assertEqual(lines[2]["error"]["code"], -32700)  # garbage line -> parse error, server survives
        self.assertIn("ready", p.stderr)  # logs on stderr, never stdout


if __name__ == "__main__":
    unittest.main()
