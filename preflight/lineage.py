"""§2 Data lineage: tables, join edges, cardinality transitions, filters."""
from __future__ import annotations

from preflight.catalog.loader import Catalog
from preflight.contracts import Lineage, LineageEdge, LineageNode
from preflight.parse.sql import ParsedSQL

_FANOUT_LABEL = {
    "high": "1:N (fan-out)",
    "medium": "1:N (moderate fan-out)",
    "low": "~1:1",
    "unknown": "unknown",
}


def build_lineage(parsed: ParsedSQL, catalog: Catalog) -> Lineage:
    nodes = []
    for table in parsed.base_tables:
        prof = catalog.profile(table)
        nodes.append(LineageNode(
            table=table,
            category=prof.category,
            volume=prof.volume,
            est_rows=prof.effective_rows() if catalog.is_known(table) else None,
        ))

    # Chain edges across base tables in declared order, labeling by catalog fan-out.
    edges = []
    tables = parsed.base_tables
    for i in range(len(tables) - 1):
        src, tgt = tables[i], tables[i + 1]
        fanout = catalog.join_fanout(src, tgt)
        if fanout == "unknown":
            fanout = catalog.join_fanout(tgt, src)
        edges.append(LineageEdge(
            source=src, target=tgt, kind="join",
            cardinality_transition=_FANOUT_LABEL.get(fanout, "unknown"),
        ))

    return Lineage(nodes=nodes, edges=edges, filters=list(parsed.filter_columns))
