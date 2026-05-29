"""Deterministic SQL structural parser built on sqlglot."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

import sqlglot
from sqlglot import exp


class PreflightParseError(ValueError):
    """Raised when the input SQL cannot be parsed."""


@dataclass
class ParsedSQL:
    base_tables: List[str] = field(default_factory=list)   # physical tables, sorted, deduped
    cte_names: List[str] = field(default_factory=list)
    join_count: int = 0
    filter_columns: List[str] = field(default_factory=list)
    has_aggregation: bool = False
    join_pairs: List[Tuple[str, str]] = field(default_factory=list)  # real join edges (table, table)


_AGG_FUNCS = {"sum", "count", "avg", "min", "max", "median", "percentile_cont"}


def _alias_map(tree: "exp.Expression") -> dict:
    """Map every table alias and name (lowercased) to the real table name."""
    mapping = {}
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        mapping[name] = name
        if t.alias:
            mapping[t.alias.lower()] = name
    return mapping


def _join_pairs(tree: "exp.Expression", cte_set: set) -> List[Tuple[str, str]]:
    """Derive physical join edges from each JOIN's ON-clause table qualifiers."""
    alias = _alias_map(tree)
    pairs: List[Tuple[str, str]] = []
    seen = set()
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if on is None:
            continue
        tables = []
        for col in on.find_all(exp.Column):
            qualifier = (col.table or "").lower()
            if not qualifier:
                continue
            real = alias.get(qualifier, qualifier)
            if real not in tables:
                tables.append(real)
        if len(tables) < 2:
            continue
        src, tgt = tables[0], tables[1]
        if src in cte_set or tgt in cte_set:
            continue
        key = frozenset((src, tgt))
        if key in seen:
            continue
        seen.add(key)
        pairs.append((src, tgt))
    return pairs


def parse_sql(query: str, dialect: str = "generic") -> ParsedSQL:
    read = None if dialect in ("generic", "", None) else dialect
    try:
        tree = sqlglot.parse_one(query, read=read)
    except sqlglot.errors.ParseError as exc:
        raise PreflightParseError(f"Could not parse SQL: {exc}") from exc
    if tree is None:
        raise PreflightParseError("Could not parse SQL: empty parse result")

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
        join_pairs=_join_pairs(tree, cte_set),
    )
