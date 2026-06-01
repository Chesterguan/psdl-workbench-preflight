"""§6 Optimization recommendations. Structured, deterministic. No LLM."""
from __future__ import annotations

from typing import List

from preflight.catalog.loader import Catalog
from preflight.contracts import Optimization, ScaleEstimate
from preflight.parse.sql import ParsedSQL


def recommend(parsed: ParsedSQL, catalog: Catalog,
              scale: ScaleEstimate) -> List[Optimization]:
    recs: List[Optimization] = []
    event_tables = [t for t in parsed.base_tables
                    if catalog.profile(t).category == "clinical_event"]

    if event_tables and not parsed.filter_columns:
        recs.append(Optimization(
            action="Add a selective filter (e.g. a code or date predicate) on the event table",
            rationale=f"Unfiltered scan of {', '.join(event_tables)} reads the entire table.",
            expected_benefit="Often >90% fewer scanned rows when filtering to specific concepts.",
        ))

    out = scale.output_records or 0
    if out >= 500_000:
        recs.append(Optimization(
            action="Build a cohort prefilter before joining event tables",
            rationale=f"Estimated output is large (~{out:,} rows).",
            expected_benefit="Estimated 60% reduction in scanned records.",
        ))

    if parsed.join_count >= 4:
        recs.append(Optimization(
            action="Reduce join depth or stage intermediate results",
            rationale=f"{parsed.join_count} joins increase intermediate cardinality.",
            expected_benefit="Lower peak memory and intermediate row counts.",
        ))

    if len(event_tables) >= 2:
        recs.append(Optimization(
            action="Narrow the observation scope to required domains",
            rationale=f"Multiple high-volume event tables touched: {', '.join(event_tables)}.",
            expected_benefit="Fewer tables scanned; smaller intermediate sets.",
        ))

    return recs
