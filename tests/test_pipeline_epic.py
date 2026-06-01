from preflight.contracts import GeneratedSQL, RiskLevel
from preflight.catalog.loader import load_catalog
from preflight.pipeline import run_preflight
from fixtures.build_omop import load_query


def test_epic_clarity_pipeline_report():
    sql = GeneratedSQL(query=load_query("epic_or_cases"), dialect="tsql", target="clarity")
    report = run_preflight(sql, load_catalog("clarity"))

    assert report.summary.execution_target == "clarity"
    assert "ORDER_PROCEDURE_DTL" in report.summary.tables
    assert report.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert report.scale.encounters is not None          # encounter rollup populated (review M1)
    assert report.bottlenecks[0].component == "ORDER_PROCEDURE_DTL"  # highest-volume table
    assert report.optimizations                          # 1.2B * 0.001 = 1.2M output -> rec
