"""Postgres live connector. EXPLAIN (FORMAT JSON) carries Plan Rows; no execution."""
from __future__ import annotations

from typing import List

from preflight.connector.base import PlanFacts
from preflight.contracts import PlanNode

_SEQ_SCAN_HINT_THRESHOLD = 1000


def parse_pg_plan(plan_json: List[dict]) -> PlanFacts:
    root = plan_json[0]["Plan"]
    nodes: List[PlanNode] = []
    hints: List[str] = []

    def walk(node: dict):
        op = node.get("Node Type", "?")
        pn = PlanNode(
            op=op,
            table=node.get("Relation Name"),
            estimated_rows=node.get("Plan Rows"),
            scan_type=op if "Scan" in op else None,
            join_type=node.get("Join Type") if "Join" in op else None,
            index_used=("Index" in op) or ("Index Name" in node),
        )
        nodes.append(pn)
        if op == "Seq Scan" and (node.get("Plan Rows") or 0) > _SEQ_SCAN_HINT_THRESHOLD:
            rel = node.get("Relation Name", "?")
            filt = node.get("Filter", "")
            hints.append(f"Sequential scan on {rel}; consider an index{(' for ' + filt) if filt else ''}")
        for child in node.get("Plans", []) or []:
            walk(child)

    walk(root)
    return PlanFacts(nodes=nodes, total_estimated_rows=root.get("Plan Rows"),
                     missing_index_hints=hints)


class PostgresConnector:
    def __init__(self, dsn: str):
        self._dsn = dsn

    def analyze(self, sql: str) -> PlanFacts:
        import psycopg
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("EXPLAIN (FORMAT JSON) " + sql)
                plan_json = cur.fetchone()[0]
        return parse_pg_plan(plan_json)
