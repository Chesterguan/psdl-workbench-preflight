"""DuckDB live connector. Parses text EXPLAIN (no execution)."""
from __future__ import annotations

import re
from typing import List, Optional

from preflight.connector.base import PlanFacts
from preflight.contracts import PlanNode

# Matches row estimates like "~1,020 rows" or "~100 rows"
_ROWS_RE = re.compile(r"~([\d,]+)\s+rows")
# Matches a line that contains a node label (all-caps word with underscores, >=3 chars)
# surrounded by box-drawing whitespace — e.g. "│         HASH_JOIN         │"
_NODE_RE = re.compile(r"[│|]\s+([A-Z][A-Z_]{2,})\s+[│|]")
# Lines that look like node labels but are not op names
_NOT_OP = {"INNER", "OUTER", "LEFT", "RIGHT", "JOIN"}
_JOIN_TYPE_RE = re.compile(r"Join Type:\s*([A-Z]+)")
_TABLE_RE = re.compile(r"Table:\s*([A-Za-z0-9_]+)")


class DuckDBConnector:
    def __init__(self, connection):
        self._con = connection

    def analyze(self, sql: str) -> PlanFacts:
        rows = self._con.execute("EXPLAIN " + sql).fetchall()
        text = "\n".join(r[1] for r in rows if len(r) > 1)
        return _parse_text_plan(text)


def _parse_text_plan(text: str) -> PlanFacts:
    nodes: List[PlanNode] = []
    total: Optional[int] = None
    lines = text.splitlines()

    for line in lines:
        # --- detect op node label ---
        node_m = _NODE_RE.search(line)
        if node_m:
            label = node_m.group(1)
            if label not in _NOT_OP:
                node = PlanNode(op=label)
                if "SCAN" in label:
                    node.scan_type = label
                if "JOIN" in label:
                    node.join_type = "INNER"
                nodes.append(node)
                continue  # row count on same line is for the parent box, skip

        if nodes:
            jt = _JOIN_TYPE_RE.search(line)
            if jt:
                nodes[-1].join_type = jt.group(1)

            tb = _TABLE_RE.search(line)
            if tb:
                nodes[-1].table = tb.group(1)

            rm = _ROWS_RE.search(line)
            if rm:
                val = int(rm.group(1).replace(",", ""))
                nodes[-1].estimated_rows = val
                if total is None:
                    total = val

    return PlanFacts(nodes=nodes, total_estimated_rows=total)
