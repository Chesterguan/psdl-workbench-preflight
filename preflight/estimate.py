"""§3 Scale + cost engine. Catalog heuristic baseline, optional live-plan override."""
from __future__ import annotations

from typing import Optional, Tuple

from preflight.catalog.loader import Catalog
from preflight.contracts import Confidence, RuntimeCategory, ScaleEstimate, StageEstimate
from preflight.parse.sql import ParsedSQL

_FAST_MAX = 100_000
_MODERATE_MAX = 5_000_000
_HEAVY_MAX = 1_000_000_000
_DEFAULT_FILTER_SELECTIVITY = 0.1


def runtime_for_rows(rows: int) -> RuntimeCategory:
    if rows < _FAST_MAX:
        return RuntimeCategory.FAST
    if rows < _MODERATE_MAX:
        return RuntimeCategory.MODERATE
    if rows < _HEAVY_MAX:
        return RuntimeCategory.HEAVY
    return RuntimeCategory.EXTREME


def _selectivity(parsed: ParsedSQL, catalog: Catalog) -> float:
    factors = []
    for col in parsed.filter_columns:
        for tbl in parsed.base_tables:
            sel = catalog.selectivity(f"{tbl}.{col}")
            if sel is not None:
                factors.append(sel)
                break
    if factors:
        result = 1.0
        for f in factors:
            result *= f
        return result
    return _DEFAULT_FILTER_SELECTIVITY if parsed.filter_columns else 1.0


def estimate_scale(
    parsed: ParsedSQL,
    catalog: Catalog,
    catalog_known_ratio: float,
    plan_rows: Optional[int] = None,
) -> Tuple[ScaleEstimate, RuntimeCategory]:
    rows_by_table = {t: catalog.profile(t).effective_rows() for t in parsed.base_tables}

    driver_rows = max(rows_by_table.values()) if rows_by_table else 0

    def _rows_for_category(cat_name: str) -> Optional[int]:
        vals = [rows_by_table[t] for t in parsed.base_tables
                if catalog.profile(t).category == cat_name]
        return max(vals) if vals else None

    patients = _rows_for_category("demographics")
    encounters = _rows_for_category("encounter")
    events = _rows_for_category("clinical_event")

    selectivity = _selectivity(parsed, catalog)
    intermediate = int(driver_rows)
    output = int(driver_rows * selectivity)
    if parsed.has_aggregation and patients:
        output = min(output, patients)

    per_stage = [
        StageEstimate(name="driver_scan", est_rows=int(driver_rows)),
        StageEstimate(name="after_filter", est_rows=output),
    ]

    confidence = Confidence.LOW if catalog_known_ratio < 0.5 else Confidence.MEDIUM
    if plan_rows is not None:
        output = int(plan_rows)
        confidence = Confidence.HIGH

    scale = ScaleEstimate(
        patients=patients,
        encounters=encounters,
        events=events,
        intermediate_records=intermediate,
        output_records=output,
        per_stage=per_stage,
        confidence=confidence,
    )
    return scale, runtime_for_rows(output)
