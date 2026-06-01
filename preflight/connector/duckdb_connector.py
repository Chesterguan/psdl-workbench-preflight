"""DuckDB live connector. Parses text EXPLAIN (no execution)."""
from __future__ import annotations

import re
from typing import List, Optional

from preflight.connector.base import PlanFacts
from preflight.contracts import PlanNode

# Matches row estimates like "~1,020 rows" or "~100 rows"
_ROWS_RE = re.compile(r"~([\d,]+)\s+rows")
# Matches a node label (all-caps word with underscores, >=3 chars) inside box-drawing bars.
# Used with finditer to capture ALL occurrences on a line (side-by-side boxes).
_NODE_RE = re.compile(r"[│|]\s+([A-Z][A-Z_]{2,})\s+[│|]")
# Lines that look like node labels but are not op names
_NOT_OP = {"INNER", "OUTER", "LEFT", "RIGHT", "JOIN"}
_JOIN_TYPE_RE = re.compile(r"Join Type:\s*([A-Z]+)")
_TABLE_RE = re.compile(r"Table:\s*([A-Za-z0-9_]+)")

# Column-split: two adjacent DuckDB boxes share the boundary  ┘┌  or  ┘└  or  │├  (the │
# after the left box closing and before the right box opening).  We split each line at
# these boundaries to associate table/row data with the correct box.
_COL_SPLIT_RE = re.compile(r"[┘└][┌└]|[┘└]\s*[┌└]|│[│├]")


def _split_columns(line: str) -> List[str]:
    """Split a line into per-box columns at ┘┌ / ┘└ / ││ / │├ boundaries.

    Returns a list of segments; for a single-box line returns [line].
    """
    # Find all split positions
    segments: List[str] = []
    prev = 0
    for m in _COL_SPLIT_RE.finditer(line):
        # The split character is one char wide (in terms of codepoint, not bytes).
        # We keep the left │ of the right box in the right segment.
        mid = m.start() + 1  # after the closing ┘/└/│ of the left column
        segments.append(line[prev:mid])
        prev = mid
    segments.append(line[prev:])
    return segments if len(segments) > 1 else [line]


class DuckDBConnector:
    def __init__(self, connection):
        self._con = connection

    def analyze(self, sql: str) -> PlanFacts:
        rows = self._con.execute("EXPLAIN " + sql).fetchall()
        text = "\n".join(r[1] for r in rows if len(r) > 1)
        return _parse_text_plan(text)


def _parse_text_plan(text: str) -> PlanFacts:  # noqa: C901
    """Parse the DuckDB text EXPLAIN output into PlanFacts.

    DuckDB 1.4.4 renders sibling operators as two boxes side-by-side on the same
    text lines.  We use finditer (not .search) to capture ALL node labels per line,
    and split each line at box-boundary characters to associate table/rows with the
    correct node box.
    """
    nodes: List[PlanNode] = []
    lines = text.splitlines()

    # We process nodes in "pending" fashion: when we detect a new op label we open
    # a new PlanNode; subsequent attribute lines (Table:, ~rows) fill the most
    # recently opened node for that column position.
    #
    # Strategy:
    #   Pass 1 – collect all (line_index, col_index, label) node label positions.
    #   Pass 2 – for each subsequent line until the next top-level operator line,
    #            split into columns and route Table:/rows to the right node.
    #
    # Because side-by-side boxes always appear together in the same group of lines,
    # we can track a list of "current column nodes" and match by column index.

    # We'll use a simpler streaming approach: maintain a list of "open slots" where
    # each slot corresponds to one box column currently being parsed.  When we see
    # node labels we open new slots; when we see table/rows we fill them by column.

    col_nodes: List[Optional[PlanNode]] = []  # one entry per side-by-side column

    for line in lines:
        if not line.strip():
            continue

        # Check if this line contains node labels (detect by finditer)
        node_matches = list(_NODE_RE.finditer(line))
        if node_matches:
            labels = [m.group(1) for m in node_matches if m.group(1) not in _NOT_OP]
            if labels:
                # Start a new group of column nodes
                new_col_nodes: List[Optional[PlanNode]] = []
                for label in labels:
                    node = PlanNode(op=label)
                    if "SCAN" in label:
                        node.scan_type = label
                    if "JOIN" in label:
                        node.join_type = "INNER"
                    nodes.append(node)
                    new_col_nodes.append(node)
                col_nodes = new_col_nodes
                continue  # Don't parse table/rows from the label line itself

        # Attribute line — split into columns and fill each column's node
        if not col_nodes:
            continue

        if len(col_nodes) == 1:
            # Single-box context: search entire line
            seg = line
            node = col_nodes[0]
            jt = _JOIN_TYPE_RE.search(seg)
            if jt:
                node.join_type = jt.group(1)
            tb = _TABLE_RE.search(seg)
            if tb:
                node.table = tb.group(1)
            rm = _ROWS_RE.search(seg)
            if rm:
                val = int(rm.group(1).replace(",", ""))
                node.estimated_rows = val
        else:
            # Multi-box context: split line by column boundaries
            segs = _split_columns(line)
            # Pad or trim to match col_nodes length
            for i, node in enumerate(col_nodes):
                if node is None:
                    continue
                seg = segs[i] if i < len(segs) else ""
                jt = _JOIN_TYPE_RE.search(seg)
                if jt:
                    node.join_type = jt.group(1)
                tb = _TABLE_RE.search(seg)
                if tb:
                    node.table = tb.group(1)
                rm = _ROWS_RE.search(seg)
                if rm:
                    val = int(rm.group(1).replace(",", ""))
                    node.estimated_rows = val

    # total_estimated_rows: use the max of all node estimated_rows (the root/topmost
    # operator usually has the highest value, but max is robust regardless of order).
    all_rows = [n.estimated_rows for n in nodes if n.estimated_rows is not None]
    total: Optional[int] = max(all_rows) if all_rows else None

    return PlanFacts(nodes=nodes, total_estimated_rows=total)
