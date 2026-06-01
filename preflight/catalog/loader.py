"""Load schema-family metadata catalogs (pure data). No DB access."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional

import yaml

_SCHEMAS_DIR = os.path.join(os.path.dirname(__file__), "schemas")

_VOLUME_ROWS = {  # fallback row estimates when row_estimate is absent
    "tiny": 1_000,
    "small": 100_000,
    "medium": 2_000_000,
    "large": 50_000_000,
    "huge": 1_000_000_000,
    "unknown": 1_000_000,
}


@dataclass
class TableProfile:
    name: str
    category: str = "unknown"
    volume: str = "unknown"
    risk: str = "unknown"
    row_estimate: Optional[int] = None

    def effective_rows(self) -> int:
        if self.row_estimate is not None:
            return self.row_estimate
        return _VOLUME_ROWS.get(self.volume, _VOLUME_ROWS["unknown"])


class Catalog:
    def __init__(self, schema: str, tables: Dict[str, TableProfile],
                 joins: Dict[str, str], columns: Dict[str, float],
                 default_dialect: str = "generic"):
        self.schema = schema
        self._tables = tables
        self._joins = joins
        self._columns = columns
        self.default_dialect = default_dialect

    def is_known(self, table: str) -> bool:
        return table.lower() in self._tables

    def profile(self, table: str) -> TableProfile:
        key = table.lower()
        if key in self._tables:
            return self._tables[key]
        return TableProfile(name=table)  # generic fallback (all "unknown")

    def join_fanout(self, source: str, target: str) -> str:
        return self._joins.get(f"{source.lower()}->{target.lower()}", "unknown")

    def selectivity(self, column: str) -> Optional[float]:
        return self._columns.get(column.lower())


def _catalog_search_dirs(catalog_dir: Optional[str] = None):
    dirs = []
    if catalog_dir:
        dirs.append(os.path.expanduser(catalog_dir))
    env_dir = os.environ.get("PREFLIGHT_CATALOG_DIR")
    if env_dir:
        dirs.append(os.path.expanduser(env_dir))
    dirs.append(os.path.expanduser("~/.preflight/catalogs"))
    dirs.append(_SCHEMAS_DIR)  # packaged seeds (lowest priority)
    return dirs


def load_catalog(schema: str, catalog_dir: Optional[str] = None) -> Catalog:
    path = None
    for d in _catalog_search_dirs(catalog_dir):
        candidate = os.path.join(d, f"{schema}.yaml")
        if os.path.exists(candidate):
            path = candidate
            break
    if path is None:
        raise FileNotFoundError(f"No catalog for schema '{schema}' in any catalog dir")
    with open(path, "r") as fh:
        data = yaml.safe_load(fh) or {}

    tables: Dict[str, TableProfile] = {}
    for name, attrs in (data.get("tables") or {}).items():
        attrs = attrs or {}
        tables[name.lower()] = TableProfile(
            name=name,
            category=attrs.get("category", "unknown"),
            volume=attrs.get("volume", "unknown"),
            risk=attrs.get("risk", "unknown"),
            row_estimate=attrs.get("row_estimate"),
        )

    joins = {k.lower(): v for k, v in (data.get("joins") or {}).items()}
    columns = {k.lower(): float(v) for k, v in (data.get("columns") or {}).items()}
    return Catalog(schema=data.get("schema", schema), tables=tables, joins=joins,
                   columns=columns, default_dialect=data.get("default_dialect", "generic"))
