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

    # Edges follow the actual join graph (from ON-clause table qualifiers). Fall back
    # to chaining base tables in order only when no explicit join pairs were found
    # (e.g. implicit/comma joins).
    base_set = set(parsed.base_tables)
    if parsed.join_pairs:
        pairs = [(s, t) for (s, t) in parsed.join_pairs if s in base_set and t in base_set]
    else:
        tables = parsed.base_tables
        pairs = [(tables[i], tables[i + 1]) for i in range(len(tables) - 1)]

    edges = []
    for src, tgt in pairs:
        fanout = catalog.join_fanout(src, tgt)
        if fanout == "unknown":
            fanout = catalog.join_fanout(tgt, src)
        edges.append(LineageEdge(
            source=src, target=tgt, kind="join",
            cardinality_transition=_FANOUT_LABEL.get(fanout, "unknown"),
        ))

    return Lineage(nodes=nodes, edges=edges, filters=list(parsed.filter_columns))
