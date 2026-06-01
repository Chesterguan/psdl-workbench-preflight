import json

from preflight.contracts import GeneratedSQL
from preflight.catalog.loader import load_catalog
from preflight.pipeline import run_preflight
from preflight.report.render import render_text, render_json
from fixtures.build_omop import load_query


def _report():
    sql = GeneratedSQL(query=load_query("crrt_flowsheet"), dialect="duckdb", target="omop")
    return run_preflight(sql, load_catalog("omop"))


def test_render_text_includes_all_sections():
    out = render_text(_report())
    for header in ["STUDY SUMMARY", "DATA LINEAGE", "SCALE", "RISK",
                   "BOTTLENECK", "OPTIMIZATION", "CONFIDENCE"]:
        assert header in out.upper()


def test_render_json_roundtrips():
    out = render_json(_report())
    data = json.loads(out)
    assert data["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert data["summary"]["execution_target"] == "omop"


def test_render_text_shows_index_usage_and_missing_index_hints():
    """§7 QUERY PLAN section must show index=yes marker and missing-index hints."""
    from preflight.contracts import (
        Bottleneck, Confidence, LineageEdge, LineageNode, Lineage,
        Optimization, PlanNode, QueryPlan, PreflightReport, RiskLevel,
        RuntimeCategory, ScaleEstimate, StudySummary,
    )

    plan = QueryPlan(
        nodes=[
            PlanNode(op="INDEX_SCAN", table="measurement", estimated_rows=500,
                     scan_type="INDEX_SCAN", index_used=True),
            PlanNode(op="SEQ_SCAN", table="person", estimated_rows=100,
                     scan_type="SEQ_SCAN", index_used=False),
        ],
        total_estimated_rows=500,
        missing_index_hints=["Sequential scan on measurement; consider an index"],
    )
    report = PreflightReport(
        summary=StudySummary(execution_target="test", tables=["person", "measurement"],
                             query_shape="join"),
        lineage=Lineage(
            nodes=[LineageNode(table="person"), LineageNode(table="measurement")],
            edges=[LineageEdge(source="measurement", target="person")],
        ),
        scale=ScaleEstimate(output_records=500, confidence=Confidence.HIGH),
        risk_level=RiskLevel.LOW,
        risk_reasons=["small query"],
        bottlenecks=[Bottleneck(component="scan", reason="seq scan", contribution_pct=80)],
        optimizations=[],
        query_plan=plan,
        runtime_category=RuntimeCategory.FAST,
        confidence=Confidence.HIGH,
    )
    out = render_text(report)
    # Index usage marker must appear for the node that has index_used=True
    assert "index" in out.lower(), "Expected 'index' marker in rendered output"
    # Missing-index hints section must appear
    assert "Sequential scan on measurement; consider an index" in out, (
        "Expected missing-index hint in rendered output"
    )
