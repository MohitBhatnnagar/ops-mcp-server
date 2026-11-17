"""Operations tools exposed over MCP.

Design rules: read tools are side-effect free; the one write tool only *creates a
pending approval* (it never performs the business action); all file access is
confined to the data directory.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import uuid
from collections import Counter
from pathlib import Path

from .registry import Registry, ToolError


def build_registry(data_dir: str | Path, db_path: str = ":memory:") -> Registry:
    root = Path(data_dir).resolve()
    reg = Registry()
    db = sqlite3.connect(db_path, check_same_thread=False)
    db.execute("CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, action TEXT, "
               "reason TEXT, requested_by TEXT, status TEXT, created REAL)")

    def load_json(name: str):
        p = (root / name).resolve()
        if root not in p.parents:  # path traversal guard
            raise ToolError("access outside the data directory is not allowed")
        if not p.exists():
            raise ToolError(f"data file not found: {name}")
        return json.loads(p.read_text(encoding="utf-8"))

    @reg.register("kpi_status", "Return current value, target and red/amber/green status for one KPI, or all KPIs.",
                  {"type": "object", "properties": {"kpi": {"type": "string", "maxLength": 64}}})
    def kpi_status(a: dict) -> str:
        kpis = load_json("kpis.json")
        want = a.get("kpi")
        rows = []
        for k in kpis:
            if want and k["name"].lower() != want.lower():
                continue
            val, tgt, lower = k["value"], k["target"], k.get("lower_is_better", False)
            gap = (val - tgt) / tgt if lower else (tgt - val) / tgt
            status = "green" if gap <= 0 else "amber" if gap <= 0.1 else "red"
            rows.append(f"{k['name']}: {val} (target {tgt}) -> {status}")
        if not rows:
            raise ToolError(f"no KPI named {want!r}; available: " + ", ".join(k["name"] for k in kpis))
        return "\n".join(rows)

    @reg.register("capacity_check", "Compare assigned hours to capacity for each team and flag over-allocation.",
                  {"type": "object", "properties": {"threshold": {"type": "number", "minimum": 0.5, "maximum": 1.5}}})
    def capacity_check(a: dict) -> str:
        limit = a.get("threshold", 0.9)
        lines = []
        for team in load_json("teams.json"):
            load = team["assigned_hours"] / team["capacity_hours"]
            flag = "OVER" if load > 1.0 else "HIGH" if load > limit else "ok"
            lines.append(f"{team['team']}: {load:.0%} ({flag})")
        return "\n".join(lines)

    @reg.register("search_runbooks", "Search operational runbooks and return the best matching titles with a snippet.",
                  {"type": "object", "required": ["query"],
                   "properties": {"query": {"type": "string", "minLength": 2, "maxLength": 200},
                                  "limit": {"type": "integer", "minimum": 1, "maximum": 5}}})
    def search_runbooks(a: dict) -> str:
        docs = {p.stem: p.read_text(encoding="utf-8") for p in sorted((root / "runbooks").glob("*.md"))}
        if not docs:
            raise ToolError("no runbooks available")
        tok = lambda s: re.findall(r"[a-z0-9]+", s.lower())
        df = Counter(t for d in docs.values() for t in set(tok(d)))
        n = len(docs)
        scored = []
        for name, text in docs.items():
            tf = Counter(tok(text))
            s = sum(tf[t] * math.log(1 + n / df[t]) for t in tok(a["query"]) if t in df)
            if s > 0:
                scored.append((s, name, text))
        if not scored:
            return "no matching runbooks"
        scored.sort(key=lambda x: (-x[0], x[1]))
        return "\n\n".join(f"[{name}] {text.strip().splitlines()[0]}\n{' '.join(text.split())[:220]}..."
                           for _, name, text in scored[:a.get("limit", 3)])

    @reg.register("request_approval",
                  "Create a PENDING approval request for a business action. This does not perform the action; a human must approve it.",
                  {"type": "object", "required": ["action", "reason", "requested_by"],
                   "properties": {"action": {"type": "string", "minLength": 3, "maxLength": 120},
                                  "reason": {"type": "string", "minLength": 3, "maxLength": 500},
                                  "requested_by": {"type": "string", "minLength": 1, "maxLength": 64}}},
                  read_only=False)
    def request_approval(a: dict) -> str:
        rid = uuid.uuid4().hex[:8]
        db.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?)",
                   (rid, a["action"], a["reason"], a["requested_by"], "pending", time.time()))
        db.commit()
        return f"approval {rid} created with status pending. A human must approve before anything happens."

    return reg
