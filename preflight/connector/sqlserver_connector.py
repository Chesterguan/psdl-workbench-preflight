"""SQL Server live connector. SET SHOWPLAN_XML ON returns the ESTIMATED plan; no execution."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List, Optional

from preflight.connector.base import PlanFacts
from preflight.connector.redact import redact_literals
from preflight.contracts import PlanNode

_JOIN_OPS = {"Hash Match", "Nested Loops", "Merge Join"}
_SCAN_PREDICATE_HINT_THRESHOLD = 1000


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _strip_brackets(name: str) -> str:
    # "[Clarity].[dbo].[ORDER_PROCEDURE_DTL]" -> "ORDER_PROCEDURE_DTL"
    return name.split(".")[-1].strip("[]")


def _own_descendants(relop):
    """Descendants of a RelOp, excluding anything inside nested child RelOps."""
    for child in list(relop):
        if _local(child.tag) == "RelOp":
            continue
        yield child
        yield from _own_descendants(child)


def _to_int(val) -> Optional[int]:
    try:
        return int(round(float(val)))
    except (TypeError, ValueError):
        return None


def parse_showplan_xml(xml_text: str) -> PlanFacts:
    if not xml_text or not xml_text.strip():
        return PlanFacts()  # empty/absent plan (e.g. server returned no rows) -> no facts
    root = ET.fromstring(xml_text)
    nodes: List[PlanNode] = []
    hints: List[str] = []

    for relop in (el for el in root.iter() if _local(el.tag) == "RelOp"):
        phys = relop.get("PhysicalOp", "?")
        logical = relop.get("LogicalOp", "")
        node = PlanNode(op=phys, estimated_rows=_to_int(relop.get("EstimateRows")))
        if "Scan" in phys:
            node.scan_type = phys
        if phys in _JOIN_OPS:
            node.join_type = logical or "Join"
        if "Seek" in phys or "Index" in phys:
            node.index_used = True

        predicate_text = None
        for d in _own_descendants(relop):
            lt = _local(d.tag)
            if lt == "Object" and node.table is None and d.get("Table"):
                node.table = _strip_brackets(d.get("Table"))
            if lt == "ScalarOperator" and predicate_text is None and d.get("ScalarString"):
                predicate_text = d.get("ScalarString")
        nodes.append(node)

        if "Scan" in phys and predicate_text and (node.estimated_rows or 0) > _SCAN_PREDICATE_HINT_THRESHOLD:
            hints.append(f"Scan on {node.table or '?'}; predicate {redact_literals(predicate_text)}")

    # total estimated rows = the root RelOp (direct child of QueryPlan)
    total: Optional[int] = None
    for qp in (el for el in root.iter() if _local(el.tag) == "QueryPlan"):
        for c in list(qp):
            if _local(c.tag) == "RelOp":
                total = _to_int(c.get("EstimateRows"))
                break
        if total is not None:
            break
    if total is None and nodes:
        total = nodes[0].estimated_rows

    # SQL Server's own missing-index suggestions (column names only — no literals)
    for mi in (el for el in root.iter() if _local(el.tag) == "MissingIndex"):
        tbl = _strip_brackets(mi.get("Table", "?"))
        cols = [_strip_brackets(c.get("Name")) for c in mi.iter()
                if _local(c.tag) == "Column" and c.get("Name")]
        hints.append(f"Missing index on {tbl} ({', '.join(cols)})")

    return PlanFacts(nodes=nodes, total_estimated_rows=total, missing_index_hints=hints)


class SQLServerConnector:
    def __init__(self, dsn: str):
        self._dsn = dsn

    def analyze(self, sql: str) -> PlanFacts:
        import pyodbc  # optional dependency, imported lazily
        with pyodbc.connect(self._dsn) as conn:
            cur = conn.cursor()
            cur.execute("SET SHOWPLAN_XML ON")
            try:
                cur.execute(sql)            # NOT executed — SHOWPLAN_XML returns the plan only
                row = cur.fetchone()
                xml_text = row[0] if row else ""
            finally:
                cur.execute("SET SHOWPLAN_XML OFF")
        return parse_showplan_xml(xml_text)
