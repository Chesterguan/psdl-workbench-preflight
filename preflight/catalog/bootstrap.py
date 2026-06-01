"""Read-only catalog bootstrapper: introspect system catalogs -> catalog YAML draft.

No user-query execution and no row VALUES are read — only table names, row-count
estimates, and (optionally) column n_distinct stats. Privacy-safe (see the privacy audit).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol

import yaml

# row_estimate -> volume tier (inverse of loader._VOLUME_ROWS thresholds)
_VOLUME_TIERS = [
    (1_000, "tiny"),
    (100_000, "small"),
    (2_000_000, "medium"),
    (50_000_000, "large"),
    (1_000_000_000, "huge"),
]

_RISK_BY_VOLUME = {
    "tiny": "low", "small": "low", "medium": "medium", "large": "high", "huge": "very_high",
}

_OMOP_CATEGORY = {
    "person": "demographics",
    "visit_occurrence": "encounter",
    "visit_detail": "encounter",
    "measurement": "clinical_event",
    "observation": "clinical_event",
    "drug_exposure": "clinical_event",
    "condition_occurrence": "clinical_event",
    "procedure_occurrence": "clinical_event",
    "device_exposure": "clinical_event",
    "death": "clinical_event",
}


@dataclass
class TableStat:
    """One row of system-catalog introspection: a table and its estimated row count."""
    name: str
    row_estimate: int
    schema: str = ""


def volume_for(row_estimate: int) -> str:
    for threshold, tier in _VOLUME_TIERS:
        if row_estimate < threshold:
            return tier
    return "huge"


def category_for(name: str, heuristic: str = "generic") -> str:
    n = name.upper()
    if heuristic == "epic":
        if n.endswith("_KEY_XREF"):
            return "bridge"
        if n.startswith("ALL_"):
            if "PATIENT" in n and "IDENT" not in n and "SNAPSHOT" not in n:
                return "demographics"
            return "dimension"
        if n.endswith("_DTL"):
            return "encounter" if "ENCOUNTER" in n else "clinical_event"
        if n.endswith("FACT"):
            return "encounter" if ("ENCOUNTER" in n or "CASE" in n) else "clinical_event"
        if n.endswith("DIM"):
            return "demographics" if "PATIENT" in n else "dimension"
        return "unknown"
    if heuristic == "omop":
        return _OMOP_CATEGORY.get(name.lower(), "unknown")
    return "unknown"


def risk_for(category: str, volume: str) -> str:
    if category not in ("clinical_event", "encounter"):
        return "low"
    tier = _RISK_BY_VOLUME.get(volume, "low")
    if category == "encounter" and tier == "very_high":
        tier = "high"  # encounter-grain tables are large but less risky than raw events
    return tier


def bootstrap_catalog(stats: List[TableStat], schema: str, heuristic: str = "generic",
                      default_dialect: Optional[str] = None,
                      stats_as_of: Optional[str] = None) -> dict:
    """Turn introspected TableStats into a catalog dict (omop.yaml shape)."""
    tables = {}
    for st in stats:
        cat = category_for(st.name, heuristic)
        vol = volume_for(st.row_estimate)
        tables[st.name] = {
            "category": cat,
            "volume": vol,
            "risk": risk_for(cat, vol),
            "row_estimate": int(st.row_estimate),
        }
    doc: dict = {"schema": schema, "tables": tables}
    if default_dialect:
        doc["default_dialect"] = default_dialect
    if stats_as_of:
        doc["stats_as_of"] = stats_as_of
    return doc


def to_yaml(doc: dict) -> str:
    """Serialize a catalog dict to YAML with a review-warning header."""
    header = "# AUTO-GENERATED draft — review category/risk before committing.\n"
    return header + yaml.safe_dump(doc, sort_keys=True, default_flow_style=False)
