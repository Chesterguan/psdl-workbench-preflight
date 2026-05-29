"""Orchestrates the deterministic Preflight pipeline. The connector is the only
optional/impure dependency. No LLM anywhere."""
from __future__ import annotations

from typing import Optional

from preflight.bottleneck import find_bottlenecks
from preflight.catalog.loader import Catalog
from preflight.confidence import score_confidence
from preflight.connector.base import Connector
from preflight.contracts import GeneratedSQL, PreflightReport, StudySummary
from preflight.estimate import estimate_scale
from preflight.lineage import build_lineage
from preflight.optimize import recommend
from preflight.parse.sql import parse_sql
from preflight.risk import assess_risk


def _query_shape(parsed) -> str:
    return f"{parsed.join_count} join(s), {len(parsed.cte_names)} CTE(s)" + (
        ", aggregated" if parsed.has_aggregation else "")


def run_preflight(sql: GeneratedSQL, catalog: Catalog,
                  connector: Optional[Connector] = None) -> PreflightReport:
    parsed = parse_sql(sql.query, dialect=sql.dialect)

    known = [t for t in parsed.base_tables if catalog.is_known(t)]
    known_ratio = (len(known) / len(parsed.base_tables)) if parsed.base_tables else 0.0

    plan_rows = None
    query_plan = None
    if connector is not None:
        facts = connector.analyze(sql.query)
        query_plan = facts.to_query_plan()
        plan_rows = facts.total_estimated_rows

    domains = sorted({catalog.profile(t).category for t in parsed.base_tables})
    summary = StudySummary(
        execution_target=sql.target,
        tables=parsed.base_tables,
        domains=domains,
        query_shape=_query_shape(parsed),
    )

    lineage = build_lineage(parsed, catalog)

    scale, runtime = estimate_scale(parsed, catalog, known_ratio, plan_rows=plan_rows)

    risk_level, risk_reasons = assess_risk(parsed, catalog, scale)

    bottlenecks = find_bottlenecks(parsed, catalog)

    optimizations = recommend(parsed, catalog, scale)

    confidence = score_confidence(known_ratio, has_plan=connector is not None)

    return PreflightReport(
        summary=summary,
        lineage=lineage,
        scale=scale,
        risk_level=risk_level,
        risk_reasons=risk_reasons,
        bottlenecks=bottlenecks,
        optimizations=optimizations,
        query_plan=query_plan,
        runtime_category=runtime,
        confidence=confidence,
    )
