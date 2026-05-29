"""Deterministic SQL structural parser built on sqlglot."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import sqlglot
from sqlglot import exp


@dataclass
class ParsedSQL:
    base_tables: List[str] = field(default_factory=list)   # physical tables, sorted, deduped
    cte_names: List[str] = field(default_factory=list)
    join_count: int = 0
    filter_columns: List[str] = field(default_factory=list)
    has_aggregation: bool = False


_AGG_FUNCS = {"sum", "count", "avg", "min", "max", "median", "percentile_cont"}


def parse_sql(query: str, dialect: str = "generic") -> ParsedSQL:
    read = None if dialect in ("generic", "", None) else dialect
    tree = sqlglot.parse_one(query, read=read)

    cte_names = [cte.alias for cte in tree.find_all(exp.CTE)]
    cte_set = set(cte_names)

    all_tables = {t.name for t in tree.find_all(exp.Table)}
    base_tables = sorted(all_tables - cte_set)

    join_count = len(list(tree.find_all(exp.Join)))

    filter_columns = sorted({
        c.name for c in tree.find_all(exp.Column)
        if c.find_ancestor(exp.Where) is not None or c.find_ancestor(exp.Join) is not None
    })

    has_aggregation = (
        tree.find(exp.Group) is not None
        or any(f.sql_name().lower() in _AGG_FUNCS for f in tree.find_all(exp.Func))
    )

    return ParsedSQL(
        base_tables=base_tables,
        cte_names=sorted(cte_names),
        join_count=join_count,
        filter_columns=filter_columns,
        has_aggregation=has_aggregation,
    )
